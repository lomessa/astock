"""
推送通知
========
支持钉钉、飞书、企业微信 Webhook 推送。
"""
import json
import hmac
import hashlib
import base64
import urllib.parse
from typing import Optional
from datetime import datetime

import requests

from astock.config import get_settings


class Notifier:
    """消息推送器"""

    def __init__(self):
        self.cfg = get_settings().notify

    def _dingtalk_sign(self, timestamp: str, secret: str) -> str:
        """钉钉签名"""
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return urllib.parse.quote_plus(base64.b64encode(hmac_code))

    def send_dingtalk(self, title: str, content: str) -> bool:
        """推送钉钉"""
        if not self.cfg.dingtalk_webhook:
            return False
        timestamp = str(int(datetime.now().timestamp() * 1000))
        url = self.cfg.dingtalk_webhook
        if self.cfg.dingtalk_secret:
            sign = self._dingtalk_sign(timestamp, self.cfg.dingtalk_secret)
            url = f"{url}&timestamp={timestamp}&sign={sign}"

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {title}\n\n{content}",
            },
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            return resp.json().get("errcode") == 0
        except Exception as e:
            print(f"[WARN] 钉钉推送失败: {e}")
            return False

    def send_feishu(self, title: str, content: str) -> bool:
        """推送飞书"""
        if not self.cfg.feishu_webhook:
            return False
        payload = {
            "msg_type": "text",
            "content": {
                "text": f"{title}\n\n{content}",
            },
        }
        try:
            resp = requests.post(self.cfg.feishu_webhook, json=payload, timeout=10)
            return resp.json().get("code") == 0
        except Exception as e:
            print(f"[WARN] 飞书推送失败: {e}")
            return False

    def send_wechat(self, title: str, content: str) -> bool:
        """推送企业微信"""
        if not self.cfg.wechat_webhook:
            return False
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"**{title}**\n>{content.replace(chr(10), chr(10)+'>')}",
            },
        }
        try:
            resp = requests.post(self.cfg.wechat_webhook, json=payload, timeout=10)
            return resp.json().get("errcode") == 0
        except Exception as e:
            print(f"[WARN] 企业微信推送失败: {e}")
            return False

    def send(self, title: str, content: str, min_confidence: Optional[float] = None) -> bool:
        """
        统一推送入口

        Returns
        -------
        bool
            至少一个渠道成功返回 True
        """
        if not self.cfg.enable_push:
            return False
        if min_confidence is not None and min_confidence < self.cfg.min_confidence:
            return False

        ok = False
        if self.send_dingtalk(title, content):
            ok = True
        if self.send_feishu(title, content):
            ok = True
        if self.send_wechat(title, content):
            ok = True
        return ok
