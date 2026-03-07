import math
import itertools
import time

import requests


URL_BASE = "https://maimai.lxns.net/"
TOKEN = "rB1ComaLoVU99-KAqwHc8QMrPxRUK761IwfzkG9OkyE="
FRIENDCODE = 101762085662533


class ScoreSet(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(self, *args, **kwargs)

    def get_ajc_subset(self) -> ScoreSet:
        ajc_subset = ScoreSet()
        for index, item in enumerate([v for v in self.values() if v["if_fc"] == 3], start=1):
            ajc_subset[index] = item
        return ajc_subset
    
    def get_mas_ult_subset(self) -> ScoreSet:
        mas_ult_subset = ScoreSet()
        for index, item in enumerate([v for v in self.values() if v["level_index"] in [3,4]], start=1):
            mas_ult_subset[index] = item
        return mas_ult_subset


class Requester:
    def __init__(self, token:str, friend_code:int):
        self.headers = {"Authorization" : token}
        self.friend_code = friend_code
    
    def get_user_b50(self) -> requests.Response:
        url = f"{URL_BASE}api/v0/chunithm/player/{self.friend_code}/bests"
        return requests.get(url=url, headers=self.headers)
    
    def get_user_scores(self) -> requests.Response:
        url = f"{URL_BASE}api/v0/chunithm/player/{self.friend_code}/scores"
        return requests.get(url=url, headers=self.headers)
    
    def get_song_difficulty(self, id:int) -> requests.Response:
        url = f"{URL_BASE}api/v0/chunithm/song/{id}"
        return requests.get(url=url, headers=self.headers)


class RequestParser:
    def __init__(self, data:dict, all_score:dict):
        self.best = data["bests"]
        self.new = data["new_bests"]
        self.selections = data["selections"]
        self.all = all_score
    
    def parse_best(self) -> ScoreSet:
        best_set = ScoreSet()
        for i in range(len(self.best)):
            best_set[i+1] = {
                "id" : self.best[i]["id"],
                "song_name" : self.best[i]["song_name"],
                "level_index" : self.best[i]["level_index"],
                "difficulty" : self.parse_difficulty(Requester(TOKEN, FRIENDCODE).get_song_difficulty(self.best[i]["id"]), self.best[i]["level_index"]),
                "score" : self.best[i]["score"],
                "if_cleared" : False if self.best[i]["clear"] == "failed" else True,
                "if_fc" : self._parse_fc(self.best[i]["full_combo"]),
                "rank" : self._parse_rank(self.best[i]["rank"]),
            }
        return best_set
    
    def parse_new(self) -> ScoreSet:
        new_set = ScoreSet()
        for i in range(len(self.new)):
            new_set[i+1] = {
                "id" : self.new[i]["id"],
                "song_name" : self.new[i]["song_name"],
                "level_index" : self.new[i]["level_index"],
                "difficulty" : self.parse_difficulty(Requester(TOKEN, FRIENDCODE).get_song_difficulty(self.new[i]["id"]), self.new[i]["level_index"]),
                "score" : self.new[i]["score"],
                "if_cleared" : False if self.new[i]["clear"] == "failed" else True,
                "if_fc" : self._parse_fc(self.new[i]["full_combo"]),
                "rank" : self._parse_rank(self.new[i]["rank"]),
            }
        return new_set

    def parse_all(self) -> ScoreSet:
        all_set = ScoreSet()
        for i in range(len(self.all)):
            all_set[i+1] = {
                "id" : self.all[i]["id"],
                "level_index" : self.all[i]["level_index"],
                "level" : self.all[i]["level"],
                "if_fc" : self._parse_fc(self.all[i]["full_combo"]),
            }
        return all_set

    def _parse_fc(self, full_combo) -> int:
        if full_combo == None:
            return 0
        elif full_combo == "fullcombo":
            return 1
        elif full_combo == "alljustice":
            return 2
        elif full_combo == "alljusticecritical":
            return 3
        else:
            raise ValueError("Invalid FC status")
    
    def _parse_rank(self, rank:str) -> int:
        if rank in ['d','c','b','bb','bbb']:
            return 0
        elif rank == 'a':
            return 1
        elif rank == 'aa':
            return 2
        elif rank == 'aaa':
            return 3
        elif rank == 's':
            return 4
        elif rank == 'sp':
            return 5
        elif rank == 'ss':
            return 6
        elif rank == 'ssp':
            return 7
        elif rank == 'sss':
            return 8
        elif rank == 'sssp':
            return 9
        else:
            raise ValueError("Invalid rank status")
        
    def parse_difficulty(self, data:requests.Response, level_index:int) -> float:
        if data.status_code != 200:
            raise ValueError("Invalid response")
        return data.json()["difficulties"][level_index]["level_value"]


class ChunithmForceCalculator:
    def __init__(self, token:str=TOKEN, friend_code:int=FRIENDCODE):
        self.requester = Requester(token, friend_code)
        self.parser = RequestParser(self.requester.get_user_b50().json()["data"],
                                    self.requester.get_user_scores().json()["data"])

    def _get_best_set(self) -> ScoreSet:
        return self.parser.parse_best()
    
    def _get_new_set(self) -> ScoreSet:
        return self.parser.parse_new()
    
    def _get_ajc_set(self) -> ScoreSet:
        return self.parser.parse_all().get_ajc_subset()
    
    def _get_mas_ult_ajc_set(self) -> ScoreSet:
        return self.parser.parse_all().get_ajc_subset().get_mas_ult_subset()

    def _calculate_force_component(self, score_dict:dict) -> float:
        def calculate_score_correction(score:int) -> float:
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
                return 0.6 + math.floor((score - 990000) / 250) * 0.01
            elif score >= 975000:
                return 0.0 + math.floor((score - 975000) / 250) * 0.01
            elif score >= 950000:
                return -1.67 + math.floor((score - 950000) / 150) * 0.01
            elif score >= 925000:
                return -3.34 + math.floor((score - 925000) / 150) * 0.01
            elif score > 900000:
                return -5.0 + math.floor((score - 900000) / 150) * 0.01
            else:
                return -5.0
        
        def calculate_fc_aj_correction(if_fc:int, cleared:bool) -> float:
            if cleared:
                if if_fc == 0:
                    return 1.5
                elif if_fc == 1:
                    return 2.0
                elif if_fc == 2:
                    return 3.0
                elif if_fc == 3:
                    return 3.1
            else:
                return 0.0

        const = score_dict["difficulty"]
        score = score_dict["score"]
        cleared = score_dict["if_cleared"]
        if_fc = score_dict["if_fc"]
        
        score_correction = calculate_score_correction(score)
        fc_aj_correction = calculate_fc_aj_correction(if_fc, cleared)
        
        print(score_dict["song_name"], const, score, const + score_correction + fc_aj_correction)

        return const + score_correction + fc_aj_correction

    def _calculate_average_force(self, best_set:ScoreSet, new_set:ScoreSet) -> float:
        b30_tot = 0.0
        n20_tot = 0.0
        
        for b in best_set:
            b30_tot += self._calculate_force_component(best_set[b])
        for n in new_set:
            n20_tot += self._calculate_force_component(new_set[n])
        
        return (b30_tot + n20_tot) / 50
    
    def _calculate_ajc_avg_force(self, ajc_set:ScoreSet) -> float:
        def calculate_single_ajc_force(score_dict:dict) -> float:
            difficulties = self.requester.get_song_difficulty(score_dict["id"])
            const = self.parser.parse_difficulty(difficulties, score_dict["level_index"])
            return (const / 15.0) ** 2 * 2.0
        
        ajc_tot = 0.0
        length = min(50, len(ajc_set))
        ajc_set = dict(itertools.islice(ajc_set.items(), length))
        
        for i in ajc_set:
            ajc_tot += calculate_single_ajc_force(ajc_set[i])
            
        try:
            return ajc_tot / length
        except ZeroDivisionError:
            return 0.0

    def _calculate_mas_ult_ajc_bonus(self, mas_ult_ajc_set:ScoreSet) -> float:
        ajc_count = len(mas_ult_ajc_set)
        return ajc_count / 10000

    def calculate_chuniforce(self) -> list[float, float]:
        start_time = time.time()
        
        best_set = self._get_best_set()
        new_set = self._get_new_set()
        ajc_set = self._get_ajc_set()
        mas_ult_ajc_set = self._get_mas_ult_ajc_set()

        average_force = self._calculate_average_force(best_set, new_set)
        ajc_avg_force = self._calculate_ajc_avg_force(ajc_set)
        mas_ult_ajc_bonus = self._calculate_mas_ult_ajc_bonus(mas_ult_ajc_set)

        end_time = time.time()
        elapsed_time = end_time - start_time

        return average_force + ajc_avg_force + mas_ult_ajc_bonus, elapsed_time


if __name__ == "__main__":
    calculator = ChunithmForceCalculator()
    chuniforce, elapsed_time = calculator.calculate_chuniforce()
    print(f"Chunithm Force: {chuniforce:.4f}")
    print(f"Calculation Time: {elapsed_time:.3f} seconds")