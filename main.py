# main.py (Final Version with skill_info integration and sorting logic)

import json
from pathlib import Path
from typing import Dict, Any, List

# 导入AstrBot框架所需的核心库
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import AstrBotConfig

# =================================================================
# 1. 数据管理器 (Backend Logic)
#    - 负责加载、管理和查询世界书数据。
# =================================================================
class WorldbookManager:
    """世界书数据管理器"""
    DATA_PATH = Path(__file__).parent / "data"

    def __init__(self):
        self.DATA_PATH.mkdir(parents=True, exist_ok=True)
        self.worldbook_data = {}
        self._load_worldbook()

    def _load_worldbook(self):
        """从 data/Worldbook 目录下加载所有 .json 文件到内存中"""
        self.worldbook_data = {}
        print("[Worldbook-Manager] 开始加载世界书文件...")
        for file_path in self.DATA_PATH.rglob("*.json"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                    # 检查核心字段是否存在
                    if "keywords" in entry and "content" in entry and "pet_name" in entry:
                        self.worldbook_data[file_path.stem] = entry
                        print(f"[Worldbook-Manager]  - 已加载条目: {file_path.stem}")
                    else:
                        print(f"[Worldbook-Manager]  - [警告] 文件 {file_path.name} 格式不正确，已跳过。")
            except Exception as e:
                print(f"[Worldbook-Manager]  - [错误] 加载文件 {file_path.name} 失败: {e}")
        print(f"[Worldbook-Manager] 世界书加载完毕，共 {len(self.worldbook_data)} 个条目。")

    def find_entries_in_text(self, text: str) -> List[Dict[str, Any]]:
        """在给定的文本中查找匹配的关键词，并返回所有匹配条目的完整数据。"""
        found_entries = []
        if not text:
            return found_entries
        for entry_name, entry_data in self.worldbook_data.items():
            for keyword in entry_data.get("keywords", []):
                if keyword in text:
                    found_entries.append(entry_data)
                    break
        return found_entries

# =================================================================
# 2. 插件主类 (Frontend Logic)
#    - 负责与 AstrBot 框架交互，处理事件和指令。
# =================================================================
@register("Worldbook", "hapemxg", "世界书插件", "1.4.0") # 版本号可以升一下
class WorldbookPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.manager = WorldbookManager()
        self.lore_to_inject: Dict[str, List[Dict[str, Any]]] = {}

    # --- 核心逻辑第一步：监听消息，检测关键词并暂存 ---
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        """监听所有消息，分析并决定是否需要为这个会话暂存知识。"""
        session_id = event.unified_msg_origin
        user_message = event.message_str

        found_lore = self.manager.find_entries_in_text(user_message)

        if found_lore:
            self.lore_to_inject[session_id] = found_lore
            print(f"[Worldbook-Listener] Session {session_id}: 检测到关键词，暂存了 {len(found_lore)} 条知识。")
        else:
            if session_id in self.lore_to_inject:
                del self.lore_to_inject[session_id]
                print(f"[Worldbook-Listener] Session {session_id}: 无关键词，已清理过期知识。")

    # --- 核心逻辑第二步：在LLM请求前，执行注入 ---
    @filter.on_llm_request()
    async def inject_worldbook_context(self, event: AstrMessageEvent, req: ProviderRequest):
        """在LLM请求前，检查暂存区并注入知识。"""
        session_id = event.unified_msg_origin

        if session_id in self.lore_to_inject:
            # lore_to_add 现在是字典的列表 List[Dict]
            lore_to_add = self.lore_to_inject[session_id]

            # ======================= 核心优化点 =======================
            # 根据 "content" 字段的字符长度进行降序排序 (长的在前)
            lore_to_add.sort(key=lambda entry: len(entry.get('content', '')), reverse=True)
            # ========================================================

            lore_prompt = "[系统指令：请根据以下背景知识来理解和回复用户的提问。重要规则：一个宠物只能同时携带一种血脉效果和四个技能。]\n"

            for entry in lore_to_add:
                lore_prompt += "--- 背景知识 ---\n"
                
                # 1. 添加宠物名称
                lore_prompt += f"宠物：{entry.get('pet_name', '未知')}\n"

                # 2. 检查并格式化 skill_info (如果存在)
                if 'skill_info' in entry:
                    info = entry['skill_info']
                    power_str = f"威力: {info.get('power')}" if info.get('power') is not None else "威力: ---"
                    pp_str = f"PP: {info.get('pp', '?')}"
                    priority_str = f"先手等级: {info.get('priority', '?')}"
                    type_str = f"属性: {info.get('type', '?')}"
                    category_str = f"类型: {info.get('category', '?')}"
                    
                    # 格式化为一行简洁的数据
                    skill_info_line = f"技能数据：{power_str} | {pp_str} | {priority_str} | {type_str} | {category_str}\n"
                    lore_prompt += skill_info_line
                    lore_prompt += "\n" # 加一个空行，让格式更清晰

                # 3. 添加核心的 content 内容
                lore_prompt += f"{entry.get('content', '无内容')}\n"
                lore_prompt += "--- 背景知识结束 ---\n"

            req.system_prompt = lore_prompt + "\n" + req.system_prompt
            print(f"[Worldbook-Injector] Session {session_id}: 成功注入了 {len(lore_to_add)} 条背景知识 (已排序)。")

            del self.lore_to_inject[session_id]

    # --- 管理指令 ---
    @filter.command("重载世界书")
    async def reload_worldbook(self, event: AstrMessageEvent):
        """重新加载所有世界书文件。"""
        try:
            self.manager._load_worldbook()
            yield event.plain_result("✅ 世界书数据已成功重载。")
        except Exception as e:
            yield event.plain_result(f"⚠️ 重载世界书失败：{e}")