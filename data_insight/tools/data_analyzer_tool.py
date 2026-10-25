"""
数据分析工具 - 统计分析与归因分析

提供数据的统计分析、趋势分析、异常检测、维度下钻等能力。
支持零售/电商行业的常见分析场景。
"""

import json
from typing import Optional, Dict, Any, List
from hello_agents.tools.base import Tool, ToolParameter
from hello_agents.tools.response import ToolResponse
from hello_agents.tools.errors import ToolErrorCode


class DataAnalyzerTool(Tool):
    """数据分析工具 - 统计分析与归因分析"""

    def __init__(self, llm=None):
        """
        初始化数据分析工具

        Args:
            llm: HelloAgentsLLM 实例（用于归因分析的自然语言生成）
        """
        super().__init__(
            name="data_analyzer",
            description="数据分析工具，支持统计分析、趋势分析、异常检测、维度下钻和归因分析。"
                        "输入数据和分析类型，输出分析结果。"
        )
        self.llm = llm

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="analysis_type",
                type="string",
                description="分析类型: statistics(统计)/trend(趋势)/anomaly(异常)/drill_down(下钻)/attribution(归因)",
                required=True
            ),
            ToolParameter(
                name="data",
                type="string",
                description="待分析的数据（JSON格式字符串）",
                required=True
            ),
            ToolParameter(
                name="metric",
                type="string",
                description="分析的指标名称（如 GMV、订单量、转化率）",
                required=True
            ),
            ToolParameter(
                name="dimensions",
                type="string",
                description="分析维度（如 门店、品类、区域），逗号分隔",
                required=False
            ),
            ToolParameter(
                name="time_column",
                type="string",
                description="时间列名（用于趋势分析）",
                required=False
            )
        ]

    def run(self, parameters: dict) -> ToolResponse:
        """
        执行数据分析

        Args:
            parameters: 分析参数

        Returns:
            ToolResponse 包含分析结果
        """
        analysis_type = parameters.get("analysis_type", "")
        data_str = parameters.get("data", "[]")
        metric = parameters.get("metric", "")
        dimensions_str = parameters.get("dimensions", "")
        time_column = parameters.get("time_column", "")

        if not analysis_type:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="分析类型不能为空"
            )

        if not metric:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="指标名称不能为空"
            )

        try:
            # 解析数据
            if isinstance(data_str, str):
                data = json.loads(data_str)
            else:
                data = data_str

            if not isinstance(data, list) or len(data) == 0:
                return ToolResponse.error(
                    code=ToolErrorCode.INVALID_PARAM,
                    message="数据为空或格式不正确"
                )

            dimensions = [d.strip() for d in dimensions_str.split(",") if d.strip()] if dimensions_str else []

            # 根据分析类型分发
            if analysis_type == "statistics":
                return self._statistics_analysis(data, metric)
            elif analysis_type == "trend":
                return self._trend_analysis(data, metric, time_column)
            elif analysis_type == "anomaly":
                return self._anomaly_detection(data, metric)
            elif analysis_type == "drill_down":
                return self._drill_down_analysis(data, metric, dimensions)
            elif analysis_type == "attribution":
                return self._attribution_analysis(data, metric, dimensions)
            else:
                return ToolResponse.error(
                    code=ToolErrorCode.INVALID_PARAM,
                    message=f"不支持的分析类型: {analysis_type}"
                )

        except json.JSONDecodeError as e:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message=f"数据JSON解析失败: {str(e)}"
            )
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=f"分析执行失败: {str(e)}"
            )

    def _statistics_analysis(self, data: List[Dict], metric: str) -> ToolResponse:
        """基础统计分析"""
        import numpy as np

        values = []
        for row in data:
            if metric in row and row[metric] is not None:
                try:
                    values.append(float(row[metric]))
                except (ValueError, TypeError):
                    continue

        if not values:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message=f"指标 '{metric}' 没有有效的数值数据"
            )

        values = np.array(values)
        stats = {
            "count": len(values),
            "sum": float(np.sum(values)),
            "mean": float(np.mean(values)),
            "median": float(np.median(values)),
            "std": float(np.std(values)),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
            "q25": float(np.percentile(values, 25)),
            "q75": float(np.percentile(values, 75)),
        }

        text_parts = [
            f"📊 {metric} 统计分析结果:",
            f"  · 数据量: {stats['count']} 条",
            f"  · 总计: {stats['sum']:.2f}",
            f"  · 均值: {stats['mean']:.2f}",
            f"  · 中位数: {stats['median']:.2f}",
            f"  · 标准差: {stats['std']:.2f}",
            f"  · 最小值: {stats['min']:.2f}",
            f"  · 最大值: {stats['max']:.2f}",
            f"  · 25%分位: {stats['q25']:.2f}",
            f"  · 75%分位: {stats['q75']:.2f}",
        ]

        return ToolResponse.success(
            text="\n".join(text_parts),
            data={"metric": metric, "statistics": stats}
        )

    def _trend_analysis(self, data: List[Dict], metric: str, time_column: str) -> ToolResponse:
        """趋势分析"""
        if not time_column:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="趋势分析需要指定时间列名"
            )

        # 按时间排序
        sorted_data = sorted(data, key=lambda x: x.get(time_column, ""))

        # 获取数据中所有可用的数值字段
        available_fields = set()
        for row in sorted_data:
            for key, value in row.items():
                if key != time_column:
                    try:
                        float(value)
                        available_fields.add(key)
                    except (ValueError, TypeError):
                        pass

        values = []
        times = []
        for row in sorted_data:
            if metric in row and row[metric] is not None:
                try:
                    values.append(float(row[metric]))
                    times.append(str(row.get(time_column, "")))
                except (ValueError, TypeError):
                    continue

        if len(values) == 0:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message=f"指标 '{metric}' 在数据中不存在。可用的数值字段: {', '.join(available_fields)}"
            )

        if len(values) < 2:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message=f"趋势分析至少需要2个数据点，当前只有 {len(values)} 个"
            )

        # 计算环比变化
        changes = []
        for i in range(1, len(values)):
            if values[i-1] != 0:
                change_pct = (values[i] - values[i-1]) / values[i-1] * 100
            else:
                change_pct = 0
            changes.append({
                "period": f"{times[i-1]} → {times[i]}",
                "value_prev": values[i-1],
                "value_curr": values[i],
                "change": values[i] - values[i-1],
                "change_pct": round(change_pct, 2)
            })

        # 整体趋势判断
        if len(values) >= 2:
            overall_change = (values[-1] - values[0]) / values[0] * 100 if values[0] != 0 else 0
            if overall_change > 5:
                trend = "上升"
            elif overall_change < -5:
                trend = "下降"
            else:
                trend = "平稳"
        else:
            trend = "数据不足"
            overall_change = 0

        text_parts = [
            f"📈 {metric} 趋势分析:",
            f"  · 整体趋势: {trend}（累计变化 {overall_change:.2f}%）",
            f"  · 起始值: {values[0]:.2f} ({times[0]})",
            f"  · 最新值: {values[-1]:.2f} ({times[-1]})",
            "\n  环比变化:"
        ]
        for c in changes:
            direction = "↑" if c["change"] >= 0 else "↓"
            text_parts.append(
                f"    {c['period']}: {c['value_prev']:.2f} → {c['value_curr']:.2f} "
                f"({direction}{abs(c['change_pct']):.2f}%)"
            )

        return ToolResponse.success(
            text="\n".join(text_parts),
            data={
                "metric": metric,
                "trend": trend,
                "overall_change_pct": round(overall_change, 2),
                "values": values,
                "times": times,
                "changes": changes
            }
        )

    def _anomaly_detection(self, data: List[Dict], metric: str) -> ToolResponse:
        """异常检测（基于 Z-Score）"""
        import numpy as np

        values = []
        labels = []
        for i, row in enumerate(data):
            if metric in row and row[metric] is not None:
                try:
                    values.append(float(row[metric]))
                    # 尝试获取标签
                    label = row.get("name", row.get("label", f"记录{i+1}"))
                    labels.append(label)
                except (ValueError, TypeError):
                    continue

        if len(values) < 3:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="异常检测至少需要3个数据点"
            )

        values = np.array(values)
        mean = np.mean(values)
        std = np.std(values)

        # Z-Score 异常检测（阈值: 2）
        anomalies = []
        for i, (val, label) in enumerate(zip(values, labels)):
            z_score = (val - mean) / std if std > 0 else 0
            if abs(z_score) > 2:
                anomalies.append({
                    "index": i,
                    "label": label,
                    "value": float(val),
                    "z_score": round(z_score, 2),
                    "deviation": "异常高" if z_score > 0 else "异常低"
                })

        text_parts = [
            f"🔍 {metric} 异常检测结果:",
            f"  · 均值: {mean:.2f}",
            f"  · 标准差: {std:.2f}",
            f"  · 异常阈值: Z-Score > 2",
            f"  · 检测到 {len(anomalies)} 个异常值"
        ]

        if anomalies:
            text_parts.append("\n  异常详情:")
            for a in anomalies:
                text_parts.append(
                    f"    · {a['label']}: {a['value']:.2f} "
                    f"(Z-Score: {a['z_score']}, {a['deviation']})"
                )

        return ToolResponse.success(
            text="\n".join(text_parts),
            data={
                "metric": metric,
                "mean": float(mean),
                "std": float(std),
                "anomalies": anomalies,
                "anomaly_count": len(anomalies)
            }
        )

    def _drill_down_analysis(self, data: List[Dict], metric: str, dimensions: List[str]) -> ToolResponse:
        """维度下钻分析"""
        if not dimensions:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="下钻分析需要指定至少一个维度"
            )

        dim = dimensions[0]  # 取第一个维度进行下钻

        # 按维度分组聚合
        groups = {}
        for row in data:
            if dim in row and metric in row:
                key = str(row[dim])
                try:
                    value = float(row[metric])
                except (ValueError, TypeError):
                    continue
                if key not in groups:
                    groups[key] = []
                groups[key].append(value)

        # 计算每个组的统计
        import numpy as np
        group_stats = []
        for key, values in groups.items():
            arr = np.array(values)
            group_stats.append({
                "dimension": dim,
                "group": key,
                "count": len(values),
                "sum": float(np.sum(arr)),
                "mean": float(np.mean(arr)),
            })

        # 按总和降序排列
        group_stats.sort(key=lambda x: x["sum"], reverse=True)

        # 计算占比
        total_sum = sum(g["sum"] for g in group_stats)
        for g in group_stats:
            g["percentage"] = round(g["sum"] / total_sum * 100, 2) if total_sum > 0 else 0

        text_parts = [
            f"📊 {metric} 按 {dim} 维度下钻分析:",
            f"  · 共 {len(group_stats)} 个分组",
            f"  · 总计: {total_sum:.2f}",
            "\n  各组排名:"
        ]
        for i, g in enumerate(group_stats[:10]):  # 只显示前10
            text_parts.append(
                f"    {i+1}. {g['group']}: {g['sum']:.2f} "
                f"(占比 {g['percentage']:.1f}%, 均值 {g['mean']:.2f})"
            )

        return ToolResponse.success(
            text="\n".join(text_parts),
            data={
                "metric": metric,
                "dimension": dim,
                "groups": group_stats,
                "total": total_sum
            }
        )

    def _attribution_analysis(self, data: List[Dict], metric: str, dimensions: List[str]) -> ToolResponse:
        """
        归因分析 - 综合多维度下钻和异常检测

        这是"数据故事"亮点的核心能力。
        """
        import numpy as np

        if not dimensions:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="归因分析需要指定至少一个维度"
            )

        # 对每个维度进行下钻
        dimension_results = []
        for dim in dimensions:
            groups = {}
            for row in data:
                if dim in row and metric in row:
                    key = str(row[dim])
                    try:
                        value = float(row[metric])
                    except (ValueError, TypeError):
                        continue
                    if key not in groups:
                        groups[key] = []
                    groups[key].append(value)

            if not groups:
                continue

            # 计算每个组的均值
            group_means = {k: float(np.mean(v)) for k, v in groups.items()}
            overall_mean = float(np.mean([v for vals in groups.values() for v in vals]))

            # 找出偏离最大的组
            deviations = []
            for key, mean_val in group_means.items():
                deviation_pct = (mean_val - overall_mean) / overall_mean * 100 if overall_mean != 0 else 0
                deviations.append({
                    "dimension": dim,
                    "group": key,
                    "mean": mean_val,
                    "deviation_pct": round(deviation_pct, 2)
                })

            # 按偏离度绝对值排序
            deviations.sort(key=lambda x: abs(x["deviation_pct"]), reverse=True)
            dimension_results.append({
                "dimension": dim,
                "overall_mean": overall_mean,
                "top_deviations": deviations[:5]
            })

        # 构建归因结论
        text_parts = [
            f"🔍 {metric} 归因分析结果:",
            "\n按维度分析:"
        ]

        attribution_factors = []
        for dr in dimension_results:
            text_parts.append(f"\n  📌 维度: {dr['dimension']}（均值: {dr['overall_mean']:.2f}）")
            for dev in dr["top_deviations"][:3]:
                direction = "高于" if dev["deviation_pct"] > 0 else "低于"
                text_parts.append(
                    f"    · {dev['group']}: {dev['mean']:.2f} "
                    f"({direction}均值 {abs(dev['deviation_pct']):.1f}%)"
                )
                if abs(dev["deviation_pct"]) > 20:
                    attribution_factors.append(dev)

        if attribution_factors:
            text_parts.append("\n🎯 主要归因因素:")
            for i, factor in enumerate(attribution_factors[:3]):
                text_parts.append(
                    f"  {i+1}. {factor['dimension']}={factor['group']}: "
                    f"偏离均值 {factor['deviation_pct']:+.1f}%"
                )

        return ToolResponse.success(
            text="\n".join(text_parts),
            data={
                "metric": metric,
                "dimensions": dimensions,
                "dimension_results": dimension_results,
                "attribution_factors": attribution_factors
            }
        )
