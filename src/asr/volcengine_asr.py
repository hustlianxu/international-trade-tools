"""火山引擎豆包 ASR 2.0 云端语音识别。

优势：跨平台（Mac/Win/Linux）、开箱即用、西语+中文原生支持。
成本：0.0133 元/分钟，500 分钟/月 ≈ 6.7 元。
"""
import base64
import json
import logging
import time
from pathlib import Path

import requests

from .base import ASREngine

logger = logging.getLogger(__name__)

# 豆包录音文件识别接口（提交 + 轮询结果）
SUBMIT_URL = "https://openspeech.bytedance.com/api/v1/auc/submit"
QUERY_URL = "https://openspeech.bytedance.com/api/v1/auc/query"


class VolcengineASR(ASREngine):
    def __init__(self, config: dict):
        self.app_id = config.get("app_id", "")
        self.access_token = config.get("access_token", "")
        self.model = config.get("model", "bigmodel")
        if not self.app_id or not self.access_token:
            raise RuntimeError("火山豆包 ASR 需配置 app_id 和 access_token")

    def transcribe(self, audio_path: str | Path, language: str = "") -> str:
        audio_bytes = Path(audio_path).read_bytes()
        audio_b64 = base64.b64encode(audio_bytes).decode()

        # 提交识别任务
        submit_resp = requests.post(SUBMIT_URL, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer; {self.access_token}",
        }, json={
            "app": {"appid": self.app_id, "cluster": "volcengine_streaming_common"},
            "user": {"uid": "trade_tools"},
            "audio": {"data": audio_b64, "format": "wav"},
            "request": {
                "model": self.model,
                # 留空自动语种检测，外贸中西汉混用
                "language": language or "",
            },
        }, timeout=30)
        submit_data = submit_resp.json()
        if submit_resp.status_code != 200 or submit_data.get("code", -1) != 1000:
            raise RuntimeError(f"豆包ASR提交失败: {submit_data}")

        task_id = submit_data["data"]["task_id"]
        logger.info("[豆包ASR] 任务已提交: %s", task_id)

        # 轮询结果（通常 1-5 秒）
        for _ in range(60):
            time.sleep(1)
            query_resp = requests.post(QUERY_URL, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer; {self.access_token}",
            }, json={"app": {"appid": self.app_id}, "task_id": task_id})
            query_data = query_resp.json()
            status = query_data.get("data", {}).get("status", "")
            if status == "success":
                text = query_data["data"]["text"].strip()
                logger.info("[豆包ASR] 完成: %s", text[:100])
                return text
            elif status == "failed":
                raise RuntimeError(f"豆包ASR识别失败: {query_data}")
        raise TimeoutError("豆包ASR轮询超时（60秒）")

    def name(self) -> str:
        return f"Volcengine-豆包({self.model})"
