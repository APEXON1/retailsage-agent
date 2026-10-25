"""
图表生成工具 - 数据可视化

基于 Matplotlib 生成各类图表，支持柱状图、折线图、饼图等。
LLM 根据数据特征自动选择合适的图表类型。
"""

import os
import json
from typing import Optional, Dict, Any, List
from hello_agents.tools.base import Tool, ToolParameter
from hello_agents.tools.response import ToolResponse
from hello_agents.tools.errors import ToolErrorCode


class ChartGeneratorTool(Tool):
    """图表生成工具 - 数据可视化"""

    def __init__(self, output_dir: str = "./output/charts"):
        """
        初始化图表生成工具

        Args:
            output_dir: 图表输出目录
        """
        super().__init__(
            name="chart_generator",
            description="生成数据可视化图表。支持柱状图、折线图、饼图、散点图。"
                        "输入数据和图表类型，输出图片文件路径。"
        )
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        # 在初始化时设置 matplotlib 后端（避免重复调用 use()）
        import matplotlib
        matplotlib.use('Agg')

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="chart_type",
                type="string",
                description="图表类型: bar(柱状图)/line(折线图)/pie(饼图)/scatter(散点图)",
                required=True
            ),
            ToolParameter(
                name="data",
                type="string",
                description="图表数据（JSON格式）",
                required=True
            ),
            ToolParameter(
                name="title",
                type="string",
                description="图表标题",
                required=True
            ),
            ToolParameter(
                name="x_label",
                type="string",
                description="X轴标签",
                required=False
            ),
            ToolParameter(
                name="y_label",
                type="string",
                description="Y轴标签",
                required=False
            ),
            ToolParameter(
                name="filename",
                type="string",
                description="输出文件名（不含扩展名）",
                required=False
            )
        ]

    def run(self, parameters: dict) -> ToolResponse:
        """
        生成图表

        Args:
            parameters: 图表参数

        Returns:
            ToolResponse 包含图表文件路径
        """
        chart_type = parameters.get("chart_type", "")
        data_str = parameters.get("data", "{}")
        title = parameters.get("title", "数据图表")
        x_label = parameters.get("x_label", "")
        y_label = parameters.get("y_label", "")
        filename = parameters.get("filename", "")

        if not chart_type:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="图表类型不能为空"
            )

        try:
            # 解析数据
            if isinstance(data_str, str):
                data = json.loads(data_str)
            else:
                data = data_str

            # 生成文件名
            if not filename:
                import time
                filename = f"chart_{int(time.time())}"

            filepath = os.path.join(self.output_dir, f"{filename}.png")

            # 根据图表类型生成
            import matplotlib
            import matplotlib.pyplot as plt
            # 关闭所有旧图表，避免资源泄漏
            plt.close('all')
            # 设置中文字体
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False

            fig, ax = plt.subplots(figsize=(10, 6))

            if chart_type == "bar":
                self._generate_bar(ax, data)
            elif chart_type == "line":
                self._generate_line(ax, data)
            elif chart_type == "pie":
                self._generate_pie(ax, data)
            elif chart_type == "scatter":
                self._generate_scatter(ax, data)
            else:
                plt.close(fig)
                return ToolResponse.error(
                    code=ToolErrorCode.INVALID_PARAM,
                    message=f"不支持的图表类型: {chart_type}"
                )

            ax.set_title(title, fontsize=14, fontweight='bold')
            if x_label:
                ax.set_xlabel(x_label)
            if y_label:
                ax.set_ylabel(y_label)

            plt.tight_layout()
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            plt.close(fig)

            return ToolResponse.success(
                text=f"图表已生成: {filepath}",
                data={
                    "filepath": filepath,
                    "chart_type": chart_type,
                    "title": title
                }
            )

        except json.JSONDecodeError as e:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message=f"数据JSON解析失败: {str(e)}"
            )
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=f"图表生成失败: {str(e)}"
            )

    @staticmethod
    def _extract_label(d: Dict, index: int) -> str:
        """
        从字典中提取标签值（智能识别多种常见键名）

        优先级: label > name > category > city > region > month > 第一个字符串值 > 索引
        """
        # 常见的标签键名（按优先级排列）
        label_keys = ["label", "name", "category", "city", "region", "month",
                       "product", "product_name", "store_name", "channel", "age_group"]
        for key in label_keys:
            if key in d and d[key] is not None:
                return str(d[key])
        # 兜底：找第一个字符串类型的值
        for v in d.values():
            if isinstance(v, str):
                return v
        return str(index)

    @staticmethod
    def _extract_value(d: Dict) -> float:
        """
        从字典中提取数值（智能识别多种常见键名）

        优先级: value > y > total_amount > amount > order_amount > total_sales >
                count > order_count > quantity > 第一个数值 > 0
        """
        # 常见的数值键名（按优先级排列）
        value_keys = ["value", "y", "total_amount", "amount", "order_amount",
                       "total_sales", "count", "order_count", "quantity",
                       "total_orders", "gmv", "sales", "revenue", "total"]
        for key in value_keys:
            if key in d and d[key] is not None:
                try:
                    v = float(d[key])
                    if not (v != v):  # 排除 NaN
                        return v
                except (ValueError, TypeError):
                    continue
        # 兜底：找第一个数值类型的值
        for v in d.values():
            if isinstance(v, (int, float)):
                fv = float(v)
                if not (fv != fv):  # 排除 NaN
                    return fv
        return 0.0

    def _generate_bar(self, ax, data):
        """生成柱状图"""
        if isinstance(data, dict) and "labels" in data and "values" in data:
            labels = data["labels"]
            values = data["values"]
            colors = data.get("colors", None)
            ax.bar(labels, values, color=colors)
        elif isinstance(data, list):
            labels = [self._extract_label(d, i) for i, d in enumerate(data)]
            values = [self._extract_value(d) for d in data]
            ax.bar(labels, values)

    def _generate_line(self, ax, data):
        """生成折线图"""
        if isinstance(data, dict) and "x" in data and "y" in data:
            ax.plot(data["x"], data["y"], marker='o', linewidth=2)
            if "series" in data:
                for series in data["series"]:
                    ax.plot(series["x"], series["y"], marker='o', label=series.get("label", ""))
                ax.legend()
        elif isinstance(data, list):
            x = [self._extract_label(d, i) for i, d in enumerate(data)]
            y = [self._extract_value(d) for d in data]
            ax.plot(x, y, marker='o', linewidth=2)

    def _generate_pie(self, ax, data):
        """生成饼图"""
        if isinstance(data, dict) and "labels" in data and "values" in data:
            ax.pie(data["values"], labels=data["labels"], autopct='%1.1f%%', startangle=90)
        elif isinstance(data, list):
            labels = [self._extract_label(d, i) for i, d in enumerate(data)]
            values = [self._extract_value(d) for d in data]
            # 过滤掉值为 0 或负数的项（饼图不支持零值）
            filtered = [(l, v) for l, v in zip(labels, values) if v > 0]
            if not filtered:
                raise ValueError("饼图数据中所有值均为 0，无法生成图表")
            labels, values = zip(*filtered)
            ax.pie(values, labels=labels, autopct='%1.1f%%', startangle=90)

    def _generate_scatter(self, ax, data):
        """生成散点图"""
        if isinstance(data, dict) and "x" in data and "y" in data:
            ax.scatter(data["x"], data["y"], alpha=0.6, edgecolors='w', s=100)
        elif isinstance(data, list):
            x = [self._extract_value(d) for d in data]
            # 散点图需要两个维度，尝试提取第二组数值
            y = [float(list(d.values())[1]) if len(d.values()) > 1 and isinstance(list(d.values())[1], (int, float)) else 0 for d in data]
            ax.scatter(x, y, alpha=0.6, edgecolors='w', s=100)
