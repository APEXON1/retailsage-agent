"""
业务记忆工具 - 用户画像与对话历史持久化

实现"业务记忆"亮点：越用越懂你。
记录用户的关注指标、常用维度、汇报习惯等。
"""

import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from hello_agents.tools.base import Tool, ToolParameter, tool_action
from hello_agents.tools.response import ToolResponse
from hello_agents.tools.errors import ToolErrorCode


class MemoryTool(Tool):
    """业务记忆工具 - 用户画像与对话历史"""

    def __init__(self, user_profiles_dir: str = "./memory/user_profiles",
                 conversation_dir: str = "./memory/conversation_history"):
        """
        初始化业务记忆工具

        Args:
            user_profiles_dir: 用户画像存储目录
            conversation_dir: 对话历史存储目录
        """
        super().__init__(
            name="memory",
            description="业务记忆工具，管理用户画像和对话历史。"
                        "支持保存用户偏好、查询用户画像、记录对话摘要。",
            expandable=True
        )
        self.user_profiles_dir = user_profiles_dir
        self.conversation_dir = conversation_dir
        os.makedirs(user_profiles_dir, exist_ok=True)
        os.makedirs(conversation_dir, exist_ok=True)

    @tool_action("memory_save_preference", "保存用户偏好（关注的指标、维度等）")
    def save_preference(self, user_id: str, key: str, value: str) -> ToolResponse:
        """
        保存用户偏好

        Args:
            user_id: 用户ID
            key: 偏好键名（如 "focus_metrics", "focus_regions"）
            value: 偏好值
        """
        profile = self._load_profile(user_id)

        if "preferences" not in profile:
            profile["preferences"] = {}

        # 支持追加到列表
        if key in profile["preferences"]:
            existing = profile["preferences"][key]
            if isinstance(existing, list) and value not in existing:
                existing.append(value)
            else:
                profile["preferences"][key] = value
        else:
            profile["preferences"][key] = value

        profile["updated_at"] = datetime.now().isoformat()
        self._save_profile(user_id, profile)

        return ToolResponse.success(
            text=f"已保存用户 {user_id} 的偏好: {key} = {value}",
            data={"user_id": user_id, "key": key, "value": value}
        )

    @tool_action("memory_get_profile", "获取用户画像")
    def get_profile(self, user_id: str) -> ToolResponse:
        """
        获取用户画像

        Args:
            user_id: 用户ID
        """
        profile = self._load_profile(user_id)

        if not profile.get("preferences"):
            return ToolResponse.success(
                text=f"用户 {user_id} 暂无画像记录",
                data={"user_id": user_id, "profile": {}}
            )

        # 构建画像描述
        prefs = profile.get("preferences", {})
        text_parts = [f"用户 {user_id} 的画像:"]

        if "focus_metrics" in prefs:
            metrics = prefs["focus_metrics"]
            if isinstance(metrics, list):
                text_parts.append(f"  · 关注指标: {', '.join(metrics)}")
            else:
                text_parts.append(f"  · 关注指标: {metrics}")

        if "focus_regions" in prefs:
            regions = prefs["focus_regions"]
            if isinstance(regions, list):
                text_parts.append(f"  · 关注区域: {', '.join(regions)}")
            else:
                text_parts.append(f"  · 关注区域: {regions}")

        if "report_style" in prefs:
            text_parts.append(f"  · 汇报风格: {prefs['report_style']}")

        if "query_count" in profile:
            text_parts.append(f"  · 历史查询次数: {profile['query_count']}")

        return ToolResponse.success(
            text="\n".join(text_parts),
            data={"user_id": user_id, "profile": profile}
        )

    @tool_action("memory_record_query", "记录用户查询（用于学习用户偏好）")
    def record_query(self, user_id: str, query: str, metrics: str = "", regions: str = "") -> ToolResponse:
        """
        记录用户查询，用于学习用户偏好

        Args:
            user_id: 用户ID
            query: 用户的查询内容
            metrics: 查询涉及的指标（逗号分隔）
            regions: 查询涉及的区域（逗号分隔）
        """
        profile = self._load_profile(user_id)

        # 更新查询次数
        profile["query_count"] = profile.get("query_count", 0) + 1

        # 记录查询历史
        if "query_history" not in profile:
            profile["query_history"] = []

        profile["query_history"].append({
            "query": query,
            "metrics": metrics,
            "regions": regions,
            "timestamp": datetime.now().isoformat()
        })

        # 只保留最近50条
        profile["query_history"] = profile["query_history"][-50:]

        # 自动学习偏好
        if metrics:
            for metric in metrics.split(","):
                metric = metric.strip()
                if metric:
                    if "focus_metrics" not in profile["preferences"]:
                        profile["preferences"]["focus_metrics"] = []
                    if metric not in profile["preferences"]["focus_metrics"]:
                        profile["preferences"]["focus_metrics"].append(metric)

        if regions:
            for region in regions.split(","):
                region = region.strip()
                if region:
                    if "focus_regions" not in profile["preferences"]:
                        profile["preferences"]["focus_regions"] = []
                    if region not in profile["preferences"]["focus_regions"]:
                        profile["preferences"]["focus_regions"].append(region)

        profile["updated_at"] = datetime.now().isoformat()
        self._save_profile(user_id, profile)

        return ToolResponse.success(
            text=f"已记录用户 {user_id} 的查询，当前查询次数: {profile['query_count']}",
            data={"user_id": user_id, "query_count": profile["query_count"]}
        )

    @tool_action("memory_save_conversation_summary", "保存对话摘要")
    def save_conversation_summary(self, user_id: str, summary: str, session_id: str = "") -> ToolResponse:
        """
        保存对话摘要

        Args:
            user_id: 用户ID
            summary: 对话摘要
            session_id: 会话ID
        """
        if not session_id:
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        filepath = os.path.join(self.conversation_dir, f"{user_id}_{session_id}.json")

        data = {
            "user_id": user_id,
            "session_id": session_id,
            "summary": summary,
            "timestamp": datetime.now().isoformat()
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return ToolResponse.success(
            text=f"已保存对话摘要: {session_id}",
            data={"session_id": session_id, "filepath": filepath}
        )

    def _load_profile(self, user_id: str) -> Dict:
        """加载用户画像"""
        filepath = os.path.join(self.user_profiles_dir, f"{user_id}.json")
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"user_id": user_id, "preferences": {}, "query_count": 0}

    def _save_profile(self, user_id: str, profile: Dict):
        """保存用户画像"""
        filepath = os.path.join(self.user_profiles_dir, f"{user_id}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)

    def run(self, parameters: dict) -> ToolResponse:
        """普通模式下的执行方法"""
        return ToolResponse.error(
            code="NOT_IMPLEMENTED",
            message="此工具需要展开使用，请使用子工具: memory_save_preference, memory_get_profile, memory_record_query, memory_save_conversation_summary"
        )

    def get_parameters(self) -> List[ToolParameter]:
        return []
