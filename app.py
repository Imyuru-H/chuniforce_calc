# src/entry.py
from typing import Any
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
import urllib
import aiohttp
import asyncio
import unicodedata
import logging
import secrets
import json
import time
import uuid
import redis.asyncio as aioredis
from datetime import datetime

from src.calc import parse_user_response, calc_force, EMPTY_SCORE
from src.utils import generate_code_verifier, generate_code_challenge
from src.ttl_dict import AsyncTTLDict
from src.database import DatabaseManager


__version__ = "0.1.0a"


temp_data_store = AsyncTTLDict(default_ttl=600)

# lifespan 里加启动清理
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化数据库
    await DatabaseManager.init_db()
    logger.info("Database initialized")
    try:
        yield
    finally:
        # 关闭数据库连接
        from src.database import engine
        await engine.dispose()
        logger.info("Database connection closed")
    
    await temp_data_store.start_cleanup()
    try:
        yield
    finally:
        if temp_data_store._cleanup_task:
            temp_data_store._cleanup_task.cancel()

app = FastAPI(lifespan=lifespan)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SESSION_KEY = "599c47bd18008f18e09ae67cd76668ba3f2a1e9c8d7b6e5a4f3c2d1e0b9a8f7"
CLIENT_ID = "b6247554-a2e8-4461-b04b-743b08e44073"
REDIRECT_URI = "http://localhost:5000/callback"  # 部署后替换
AUTHORIZE_URL = "https://maimai.lxns.net/oauth/authorize"
TOKEN_URL = "https://maimai.lxns.net/api/v0/oauth/token"
LX_BASE_URL = "https://maimai.lxns.net"
PLAYER_API_URL = f"{LX_BASE_URL}/api/v0/user/chunithm/player"


# 配置 FastAPI APP
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_KEY,
    session_cookie="session", # 建议显式指定名字，避免默认值冲突
    same_site="lax",          # 开发时建议加，防止浏览器不发 cookie
    https_only=False,         # 本地开发用 False，线上改 True
    max_age=3600,             # 可选，设置过期时间
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class AsyncRedisDict:
    def __init__(self, ttl: int = 60, redis_url: str = "redis://localhost:6379"):
        self.ttl = ttl
        self.redis = None
        self.redis_url = redis_url
        self.key_prefix = "my_dict:"


def build_chuniforce_html(force: float) -> str:
    # 你的原函数，完整复制过来
    def get_class_info(force: float):
        if force < 2.5:
            return [1, 1]
        adjusted = force - 2.5
        steps = adjusted / 0.5
        if force >= 14.0:
            extra_steps = max(0, (force - 14.0) / 0.25)
            steps = 13 + extra_steps
        index = int(steps)
        grade = index // 4 + 1
        sub = index % 4 + 1
        if grade > 10 or (grade == 10 and sub > 4):
            return [10, 4]
        return [grade, sub]

    class_info = get_class_info(force)
    class_map = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X"}
    emblem_text = class_map.get(class_info[0], "X")
    stars = "★" * class_info[1] + "☆" * (4 - class_info[1])

    return f"""<div id='class-card' style="display: inline-block;">
        <div id='emblem'><span class='emblem-text c{class_info[0]}'>{emblem_text}</span><span class='emblem-stars'>{stars}</span></div>
        <div id='force-detail'><span class='chuniforce-text c{class_info[0]}'>CHUNIFORCE</span><span class='chuniforce-number c{class_info[0]}'>{force:.3f}</span></div>
    </div>"""

@app.get("/", response_class=HTMLResponse)
async def home():
    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "read_player",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier
    }
    url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(query)}"
    return f'<a href="{url}">点击授权 maimai CHUNITHM 数据</a>'

@app.get("/callback")
async def callback(request: Request, code: str = Query(None), state: str = Query(None)):
    if not code or not state:
        raise HTTPException(400, "授权失败")

    verifier = state
    async with aiohttp.ClientSession() as sess:
        token_resp = await sess.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier
        })
        token_json = await token_resp.json()
        access_token = token_json.get("data", {}).get("access_token")
        if not access_token:
            raise HTTPException(400, "获取 token 失败")

        headers = {"Authorization": f"Bearer {access_token}"}

        player_resp = await sess.get(PLAYER_API_URL, headers=headers)
        player = await player_resp.json()

        scores_resp = await sess.get(f"{LX_BASE_URL}/api/v0/user/chunithm/player/scores", headers=headers)
        unparsed = await scores_resp.json()

    player["data"]["name"] = unicodedata.normalize("NFKC", player["data"].get("name", ""))

    scores = await parse_user_response(unparsed)
    calced = await calc_force(scores)
    calced.sort(key=lambda x: x["force"], reverse=True)
    ajc_list = [x for x in calced if x["fc_status"] == "ajc"]
    ajc_list.sort(key=lambda x: x["force"], reverse=True)

    b50_lst = calced[:50]
    ajc_lst = ajc_list[:50]
    ajc_count = len(ajc_list)

    total_f = sum(x["force"] for x in b50_lst)
    avg_f = total_f / len(b50_lst) if b50_lst else 0

    total_ajc = sum(x["ajc_force"] for x in ajc_lst)
    avg_ajc = total_ajc / len(ajc_lst) if ajc_lst else 0

    bonus = ajc_count / 10000
    result_force = avg_f + avg_ajc + bonus

    while len(b50_lst) < 50:
        b50_lst.append(EMPTY_SCORE.copy())
    while len(ajc_lst) < 50:
        ajc_lst.append(EMPTY_SCORE.copy())

    # 生成 UUID
    user_uuid = str(uuid.uuid4())
    
    # 使用数据库保存数据
    success = await DatabaseManager.save_user_data(
        uuid=user_uuid,
        player_data=player["data"],
        b50_list=b50_lst,
        ajc_list=ajc_lst,
        ajc_count=ajc_count,
        force_result=result_force,
    )
    
    if not success:
        logger.error("Failed to save user data to database")
        raise HTTPException(500, "数据保存失败")

    return RedirectResponse(url=f"/table?uuid={user_uuid}")

@app.get("/table")
async def table_gen(request: Request, uuid: str = Query(...)):
    def build_chuniforce_html(force:float):
        def get_class_info(force: float) -> list[int]:
            if force < 2.5:
                return [1, 1]
            
            adjusted = force - 2.5
            steps = adjusted / 0.5
            
            if force >= 14.0:
                extra_steps = max(0, (force - 14.0) / 0.25)
                steps = 13 + extra_steps
            
            index = int(steps)
            
            grade = index // 4 + 1
            sub   = index % 4 + 1
            
            if grade > 10 or (grade == 10 and sub > 4):
                return [10, 4]
            
            return [grade, sub]
        
        class_info = get_class_info(force)
        class_map = {
            1:  "I",
            2:  "II",
            3:  "III",
            4:  "IV",
            5:  "V",
            6:  "VI",
            7:  "VII",
            8:  "VIII",
            9:  "IX",
            10: "X"
        }
        emblem_text = class_map.get(class_info[0])
        stars = "★" * class_info[1] + "☆" * (4 - class_info[1])

        html = f"""<div id='class-card' style="display: inline-block;">
            <div id='emblem'><span class='emblem-text c{class_info[0]}'>{emblem_text}</span><span class='emblem-stars'>{stars}</span></div>
            <div id='force-detail'><span class='chuniforce-text c{class_info[0]}'>CHUNIFORCE</span><span class='chuniforce-number c{class_info[0]}'>{force:.3f}</span></div>
        </div>"""

        return html
    
    try:
        # 从数据库获取数据
        user_data = await DatabaseManager.get_user_data(uuid)
        
        if not user_data:
            raise HTTPException(404, "数据不存在或已过期，请重新授权")
        
        # 解包数据
        player_data = user_data["player_data"]
        b50_lst = user_data["b50_list"]
        ajc_lst = user_data["ajc_list"]
        ajc_cnt = user_data["ajc_count"]
        force_result = user_data.get("force_result", 0)
        
        logger.info(f"Retrieved data for UUID: {uuid[:8]}..., ajc_count: {ajc_cnt}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving data: {e}")
        raise HTTPException(500, "数据获取失败")

    # 计算统计数据
    total_force = sum(item.get("force", 0) for item in b50_lst)
    avg_force = total_force / 50 if b50_lst else 0

    total_ajc_force = sum(item.get("ajc_force", 0) for item in ajc_lst)
    avg_ajc_force = total_ajc_force / 50 if ajc_lst else 0

    ajc_bonus = ajc_cnt / 10000

    # 确保列表长度为50
    while len(b50_lst) < 50:
        b50_lst.append(EMPTY_SCORE.copy())
    while len(ajc_lst) < 50:
        ajc_lst.append(EMPTY_SCORE.copy())

    context = {
        "request"       : request,
        "time"          : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "player"        : player_data,
        "b50_lst"       : b50_lst,
        "ajc_lst"       : ajc_lst,
        "emblem"        : build_chuniforce_html(force_result),
        "force_result"  : force_result,
        "avg_force"     : round(avg_force, 4),
        "avg_ajc_force" : avg_ajc_force,
        "ajc_bonus"     : ajc_bonus,
        "version"       : __version__,
    }
            
    return templates.TemplateResponse(
        name="table_render.html",
        context=context,
        status_code=200
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=5000, reload=True)