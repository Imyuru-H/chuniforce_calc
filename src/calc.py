# src/calc.py
from typing import List, Dict
import asyncio
from math import floor

# 全局 CONST_DICT（启动时加载）
import requests
response = requests.get("https://www.diving-fish.com/api/chunithmprober/music_data")
CONST_DICT = {item["id"]: item["ds"] for item in response.json()}

EMPTY_SCORE = {
    "clear_status": False,
    "const": 0.0,
    "fc_status": "",
    "force": 0.0,
    "ajc_force": 0.0,
    "id": 0,
    "level_index": 5,
    "score": "0000000",
    "title": "暂无数据"
}

async def get_song_const(id: int, level_index: int) -> float:
    return CONST_DICT.get(id, [0.0] * 6)[level_index]

async def score_mapping(score: int) -> float:
    if score < 900000:
        return -5.0
    elif score <= 974999:
        return floor((score - 900000) / 150) / 100 - 5.0
    elif score <= 999999:
        return floor((score - 975000) / 250) / 100
    elif score <= 1004999:
        return (score - 1000000) / 10000 + 1.0
    elif score <= 1007499:
        return (score - 1005000) / 5000 + 1.5
    elif score <= 1010000:
        return (score - 1007500) / 10000 + 2.0

async def calc_component(song: dict) -> dict[tuple[int, int], float]:
    id = song["id"]
    level_index = song["level_index"]
    const = song["const"]
    score = song["score"]
    clr_sta = song["clear_status"]
    fc_sta = song["fc_status"]
        
    ramp_map = {
        "fail" : 0.0,
        "clr" : 1.5,
        "fc" : 2.0,
        "aj" : 3.0,
        "ajc" : 3.1
    }
        
    score_corr = await score_mapping(score)
    ramp_corr = ramp_map.get("fail" if not clr_sta else fc_sta)
    force = const + score_corr + (0.0 if ramp_corr == None else ramp_corr)
        
    return {(id, level_index):max(force, 0.0)}

async def calc_force(data: List[Dict]) -> List[Dict]:
    task = [calc_component(item) for item in data]
    force_list = await asyncio.gather(*task)
    result_list = []
    for i in data:
        i["force"] = round(next((item.get((i["id"], i["level_index"])) for item in force_list if (i["id"], i["level_index"]) in item), None), 4)
        i["ajc_force"] = round(((i["const"] / 15) ** 2 * 2) if i["fc_status"] == "ajc" else 0.0)
        result_list.append(i)
    
    return result_list

async def parse_user_response(response: Dict) -> List[Dict]:
    data = response.get("data", [])
    fc_mapping = {"alljusticecritical": "ajc", "alljustice": "aj", "fullcombo": "fc", None: ""}

    const_tasks = [get_song_const(item["id"], item["level_index"]) for item in data]
    const_values = await asyncio.gather(*const_tasks)

    return [
        {
            "id": item["id"],
            "title": item["song_name"],
            "level_index": item["level_index"],
            "const": const_values[i],
            "score": item["score"],
            "clear_status": item["clear"] not in ["failed", None],
            "fc_status": fc_mapping.get(item.get("full_combo"))
        }
        for i, item in enumerate(data)
    ]