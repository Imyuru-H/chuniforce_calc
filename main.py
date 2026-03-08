import os
import itertools
import asyncio
import time
import aiohttp
from typing import Dict, List, Tuple, Optional, Any
import logging
import dotenv


# Set logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)

# Load .env
dotenv.load_dotenv()
TOKEN = os.getenv("TOKEN")
FRIENDCODE = os.getenv("FRIENDCODE")

# Define url bases
LXNS_URL_BASE = "https://maimai.lxns.net/"


class ScoreSet(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(self, *args, **kwargs)

    def get_ajc_subset(self) -> ScoreSet:
        """
        Get subset that only contains AJC scores from the original set.
        """
        
        ajc_subset = ScoreSet()
        for index, item in enumerate([v for v in self.values() if v.get("if_fc") == 3], start=1):
            ajc_subset[index] = item
        return ajc_subset
    
    def get_mas_ult_subset(self) -> ScoreSet:
        """
        Get subset that only contains scores of MASTER and ULTIMA charts from the original set.
        """
        
        mas_ult_subset = ScoreSet()
        for index, item in enumerate([v for v in self.values() if v.get("level_index") in [3,4]], start=1):
            mas_ult_subset[index] = item
        return mas_ult_subset


class AsyncRequester:
    def __init__(self, token: str, friend_code: int, session: aiohttp.ClientSession):
        self.headers = {"Authorization": token}
        self.friend_code = friend_code
        self.session = session
    
    async def get_user_b50(self) -> dict:
        """
        Get user's Best 50 chart from Lxns by friend code.
        """
        
        url = f"{LXNS_URL_BASE}api/v0/chunithm/player/{self.friend_code}/bests"
        async with self.session.get(url=url, headers=self.headers) as response:
            if response.status != 200:
                raise Exception(f"Failed to get B50 data: {response.status}")
            data = await response.json()
            return data["data"]
    
    async def get_user_scores(self) -> dict:
        """
        Get all user's scores from Lxns by friend code.
        """
        
        url = f"{LXNS_URL_BASE}api/v0/chunithm/player/{self.friend_code}/scores"
        async with self.session.get(url=url, headers=self.headers) as response:
            if response.status != 200:
                raise Exception(f"Failed to get scores data: {response.status}")
            data = await response.json()
            return data["data"]
    
    async def get_song_difficulty(self, id: int) -> Optional[dict]:
        """
        Get difficulty list from Lxns by song id.
        """
        
        url = f"{LXNS_URL_BASE}api/v0/chunithm/song/{id}"
        try:
            async with self.session.get(url=url, headers=self.headers) as response:
                if response.status != 200:
                    logger.warning(f"Failed to get song difficulty for ID {id}: {response.status}")
                    return None
                return await response.json()
        except Exception as e:
            logger.error(f"Error fetching song {id}: {e}")
            return None


class AsyncRequestParser:
    def __init__(self, requester: AsyncRequester):
        self.requester = requester
        self.difficulty_cache = {}  # Add cache to avoid repeated requests
        self.song_name_cache = {}   # Cache song title
    
    async def parse_best(self, best_data: list) -> ScoreSet:
        best_set = ScoreSet()
        tasks = []
        
        for i, item in enumerate(best_data):
            tasks.append(self._parse_song_item(i, item, is_best=True))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for idx, result in results:
            if isinstance(result, Exception):
                logger.error(f"Error parsing song at index {idx}: {result}")
                continue
            best_set[idx] = result
        
        return best_set
    
    async def parse_new(self, new_data: list) -> ScoreSet:
        new_set = ScoreSet()
        tasks = []
        
        for i, item in enumerate(new_data):
            tasks.append(self._parse_song_item(i, item, is_best=True))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for idx, result in results:
            if isinstance(result, Exception):
                logger.error(f"Error parsing new song at index {idx}: {result}")
                continue
            new_set[idx] = result
        
        return new_set

    async def _parse_song_item(self, index: int, item: dict, is_best: bool = True) -> Tuple[int, dict]:
        song_id = item["id"]
        level_index = item["level_index"]
        
        # Get song diffculty list
        difficulty = await self._get_difficulty_with_retry(song_id, level_index)
        
        # Build result dictionary
        result = {
            "id": song_id,
            "song_name": item.get("song_name", f"Unknown_{song_id}"),
            "level_index": level_index,
            "difficulty": difficulty,
            "score": item["score"],
            "if_cleared": False if item.get("clear") == "failed" else True,
            "if_fc": self._parse_fc(item.get("full_combo")),
            "rank": self._parse_rank(item.get("rank", "d")),
        }
        
        # Cache song title for later use
        self.song_name_cache[song_id] = result["song_name"]
        
        return index + 1, result

    async def _get_difficulty_with_retry(self, song_id: int, level_index: int, max_retries: int = 3) -> float:
        """Get difficulty data, with retry function."""
        cache_key = (song_id, level_index)
        
        # Check cache
        if cache_key in self.difficulty_cache:
            return self.difficulty_cache[cache_key]
        
        # Try getting difficulty data
        for attempt in range(max_retries):
            try:
                difficulty_data = await self.requester.get_song_difficulty(song_id)
                if difficulty_data and "difficulties" in difficulty_data:
                    difficulties = difficulty_data["difficulties"]
                    if level_index < len(difficulties):
                        const = difficulties[level_index].get("level_value", 0.0)
                        if const > 0:  # Ensure constant is greater than 0
                            self.difficulty_cache[cache_key] = const
                            return const
                        else:
                            logger.warning(f"Song {song_id} level {level_index} has const 0.0")
                    else:
                        logger.warning(f"Song {song_id} level_index {level_index} out of range")
                else:
                    logger.warning(f"Invalid difficulty data for song {song_id}")
                
                if attempt < max_retries - 1:
                    wait_time = 0.5 * (attempt + 1)
                    logger.info(f"Retrying song {song_id} in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed for song {song_id}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
        
        # Return defult value if all attempts failed
        logger.error(f"Failed to get difficulty for song {song_id} after {max_retries} attempts")
        return 0.0

    def parse_all(self, all_data: list) -> ScoreSet:
        all_set = ScoreSet()
        for i, item in enumerate(all_data):
            all_set[i+1] = {
                "id": item["id"],
                "level_index": item["level_index"],
                "level": item.get("level", ""),
                "if_fc": self._parse_fc(item.get("full_combo")),
            }
        return all_set

    def _parse_fc(self, full_combo) -> int:
        if full_combo is None:
            return 0
        elif full_combo == "fullcombo":
            return 1
        elif full_combo == "alljustice":
            return 2
        elif full_combo == "alljusticecritical":
            return 3
        else:
            logger.warning(f"Unknown FC status: {full_combo}, defaulting to 0")
            return 0
    
    def _parse_rank(self, rank: str) -> int:
        rank_map = {
            'd': 0, 'c': 0, 'b': 0, 'bb': 0, 'bbb': 0,
            'a': 1, 'aa': 2, 'aaa': 3,
            's': 4, 'sp': 5, 'ss': 6, 'ssp': 7, 'sss': 8, 'sssp': 9
        }
        if rank in rank_map:
            return rank_map[rank]
        logger.warning(f"Unknown rank: {rank}, defaulting to 0")
        return 0


class AsyncChunithmForceCalculator:
    def __init__(self, token: str, friend_code: int):
        self.token = token
        self.friend_code = friend_code
        self.session: Optional[aiohttp.ClientSession] = None
        self.requester: Optional[AsyncRequester] = None
        self.parser: Optional[AsyncRequestParser] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        self.requester = AsyncRequester(self.token, self.friend_code, self.session)
        self.parser = AsyncRequestParser(self.requester)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _get_best_set(self, best_data: list) -> ScoreSet:
        return await self.parser.parse_best(best_data)
    
    async def _get_new_set(self, new_data: list) -> ScoreSet:
        return await self.parser.parse_new(new_data)
    
    def _get_ajc_set(self, all_set: ScoreSet) -> ScoreSet:
        return all_set.get_ajc_subset()
    
    def _get_mas_ult_ajc_set(self, all_set: ScoreSet) -> ScoreSet:
        return all_set.get_ajc_subset().get_mas_ult_subset()

    def _calculate_force_component(self, score_dict: dict) -> float:
        def calculate_score_correction(score: int) -> float:
            if score == 1010000:
                return 2.25
            elif score >= 1009000:
                return 2.15 + (score - 1009000) * 0.0001
            elif score >= 1007500:
                return 2.0 + (score - 1007500) * 0.0001
            elif score >= 1005000:
                return 1.5 + (score - 1005000) * 0.0002
            elif score >= 1000000:
                return 1.0 + (score - 1000000) * 0.0001
            elif score >= 990000:
                return 0.6 + (score - 990000) // 250 * 0.01
            elif score >= 975000:
                return 0.0 + (score - 975000) // 250 * 0.01
            elif score >= 950000:
                return -1.67 + (score - 950000) // 150 * 0.01
            elif score >= 925000:
                return -3.34 + (score - 925000) // 150 * 0.01
            elif score > 900000:
                return -5.0 + (score - 900000) // 150 * 0.01
            else:
                return -5.0
        
        def calculate_fc_aj_correction(if_fc: int, cleared: bool) -> float:
            if cleared:
                fc_map = {0: 1.5, 1: 2.0, 2: 3.0, 3: 3.1}
                return fc_map.get(if_fc, 0.0)
            return 0.0

        const = score_dict["difficulty"]
        score = score_dict["score"]
        cleared = score_dict["if_cleared"]
        if_fc = score_dict["if_fc"]
        
        # Check if constant is 0, if so then give warning
        if const <= 0:
            logger.warning(f"Song '{score_dict['song_name']}' has const {const}, using default calculation")
        
        score_correction = calculate_score_correction(score)
        fc_aj_correction = calculate_fc_aj_correction(if_fc, cleared)
        
        total = const + score_correction + fc_aj_correction

        return total

    async def _calculate_average_force(self, best_set: ScoreSet, new_set: ScoreSet) -> float:
        # Collect all scores need to calculate
        all_items = []
        all_items.extend([(b, best_set[b]) for b in best_set])
        all_items.extend([(n, new_set[n]) for n in new_set])
        
        if not all_items:
            return 0.0
        
        # Calculate all ChuniForce component in parallel
        tasks = [self._calculate_force_component_async(item) for _, item in all_items]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out invalid results
        valid_results = [r for r in results if not isinstance(r, Exception)]
        
        if not valid_results:
            return 0.0
            
        total = sum(valid_results)
        return total / 50
    
    async def _calculate_force_component_async(self, score_dict: dict) -> float:
        # Calculate ChuniForce asynchronously
        return self._calculate_force_component(score_dict)
    
    async def _calculate_ajc_avg_force(self, ajc_set: ScoreSet) -> float:
        async def calculate_single_ajc_force(score_dict: dict) -> float:
            song_id = score_dict["id"]
            level_index = score_dict["level_index"]
            
            # Get difficulty data from cache
            const = await self.parser._get_difficulty_with_retry(song_id, level_index)
            
            if const <= 0:
                logger.warning(f"AJC song ID {song_id} has const {const}, using default value")
                return 0.0
                
            return (const / 15.0) ** 2 * 2.0
        
        if not ajc_set:
            return 0.0
            
        length = min(50, len(ajc_set))
        ajc_set_items = dict(itertools.islice(ajc_set.items(), length))
        
        tasks = [calculate_single_ajc_force(ajc_set_items[i]) for i in ajc_set_items]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out invalid value and 0 value
        valid_results = [r for r in results if not isinstance(r, Exception) and r > 0]
        
        if not valid_results:
            return 0.0
            
        return sum(valid_results) / 50

    def _calculate_mas_ult_ajc_bonus(self, mas_ult_ajc_set: ScoreSet) -> float:
        ajc_count = len(mas_ult_ajc_set)
        return ajc_count / 10000

    async def calculate_chuniforce(self) -> Tuple[Optional[float], float]:
        start_time = time.time()
        
        try:
            # Get all necessary data in parallel
            logger.info("Fetching user data...")
            b50_data, scores_data = await asyncio.gather(
                self.requester.get_user_b50(),
                self.requester.get_user_scores(),
                return_exceptions=True
            )
            
            # Check if exception exists
            if isinstance(b50_data, Exception):
                raise Exception(f"Failed to get B50 data: {b50_data}")
            if isinstance(scores_data, Exception):
                raise Exception(f"Failed to get scores data: {scores_data}")
            
            logger.info("Parsing best and new sets...")
            # Parse data
            best_set_task = self._get_best_set(b50_data.get("bests", []))
            new_set_task = self._get_new_set(b50_data.get("new_bests", []))
            
            best_set, new_set = await asyncio.gather(best_set_task, new_set_task)
            
            logger.info(f"Best set size: {len(best_set)}, New best set size: {len(new_set)}")
            
            # Parse player score
            all_set = self.parser.parse_all(scores_data)
            ajc_set = self._get_ajc_set(all_set)
            mas_ult_ajc_set = self._get_mas_ult_ajc_set(all_set)
            
            logger.info(f"Total AJC count: {len(ajc_set)}, MAS & ULT AJC count: {len(mas_ult_ajc_set)}")

            # Calculate ChuniForce of each term in parallel
            logger.info("Calculating forces...")
            average_force_task = self._calculate_average_force(best_set, new_set)
            ajc_avg_force_task = self._calculate_ajc_avg_force(ajc_set)
            
            average_force, ajc_avg_force = await asyncio.gather(
                average_force_task, 
                ajc_avg_force_task,
                return_exceptions=True
            )
            
            # Check the result
            if isinstance(average_force, Exception):
                logger.error(f"Error calculating average force: {average_force}")
                average_force = 0.0
            if isinstance(ajc_avg_force, Exception):
                logger.error(f"Error calculating AJC force: {ajc_avg_force}")
                ajc_avg_force = 0.0
            
            mas_ult_ajc_bonus = self._calculate_mas_ult_ajc_bonus(mas_ult_ajc_set)

            elapsed_time = time.time() - start_time
            
            total_force = average_force + ajc_avg_force + mas_ult_ajc_bonus
            
            logger.info(f"Average Force: {average_force:.4f}, Best 50 AJC average Force: {ajc_avg_force:.4f}, MAS & ULT AJC bonus: {mas_ult_ajc_bonus:.4f}")
            
            return total_force, elapsed_time
            
        except Exception as e:
            logger.error(f"Error calculating ChuniForce: {e}")
            return None, time.time() - start_time


async def lx_chuniforce(token: str, friendcode: int):
    async with AsyncChunithmForceCalculator(token, friendcode) as calculator:
        chuniforce, elapsed_time = await calculator.calculate_chuniforce()
        print(f"\nChuniForce: {chuniforce:.4f}")
        print(f"Calculation Time: {elapsed_time:.3f} seconds")
        
        return chuniforce, elapsed_time


if __name__ == "__main__":
    asyncio.run(lx_chuniforce(TOKEN, FRIENDCODE))