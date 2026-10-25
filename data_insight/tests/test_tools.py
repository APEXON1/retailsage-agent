"""
工具测试 - 验证各工具的 ToolResponse 输出
"""

import sys
import io
from pathlib import Path

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目路径
project_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_dir))


def test_data_analyzer():
    """测试数据分析工具"""
    print("=" * 60)
    print("测试 DataAnalyzerTool")
    print("=" * 60)

    from data_insight.tools.data_analyzer_tool import DataAnalyzerTool

    analyzer = DataAnalyzerTool()

    # 测试数据
    test_data = [
        {"region": "华东", "gmv": 1000},
        {"region": "华北", "gmv": 800},
        {"region": "华南", "gmv": 1200},
        {"region": "华西", "gmv": 600},
        {"region": "华中", "gmv": 900},
    ]

    import json

    # 测试统计分析
    print("\n1. 统计分析:")
    result = analyzer.run({
        "analysis_type": "statistics",
        "data": json.dumps(test_data),
        "metric": "gmv"
    })
    print(f"   状态: {result.status.value}")
    print(f"   结果:\n{result.text}")

    # 测试异常检测
    print("\n2. 异常检测:")
    result = analyzer.run({
        "analysis_type": "anomaly",
        "data": json.dumps(test_data),
        "metric": "gmv"
    })
    print(f"   状态: {result.status.value}")
    print(f"   结果:\n{result.text}")

    # 测试下钻分析
    print("\n3. 下钻分析:")
    result = analyzer.run({
        "analysis_type": "drill_down",
        "data": json.dumps(test_data),
        "metric": "gmv",
        "dimensions": "region"
    })
    print(f"   状态: {result.status.value}")
    print(f"   结果:\n{result.text}")

    print("\n✅ DataAnalyzerTool 测试完成")


def test_chart_generator():
    """测试图表生成工具"""
    print("\n" + "=" * 60)
    print("测试 ChartGeneratorTool")
    print("=" * 60)

    import json
    import tempfile
    import os

    from data_insight.tools.chart_generator_tool import ChartGeneratorTool

    with tempfile.TemporaryDirectory() as tmpdir:
        chart_tool = ChartGeneratorTool(output_dir=tmpdir)

        # 测试柱状图
        print("\n1. 柱状图:")
        result = chart_tool.run({
            "chart_type": "bar",
            "data": json.dumps({
                "labels": ["华东", "华北", "华南", "华西", "华中"],
                "values": [1000, 800, 1200, 600, 900]
            }),
            "title": "各区域GMV对比",
            "x_label": "区域",
            "y_label": "GMV（万元）",
            "filename": "test_bar"
        })
        print(f"   状态: {result.status.value}")
        print(f"   结果: {result.text}")

        # 测试折线图
        print("\n2. 折线图:")
        result = chart_tool.run({
            "chart_type": "line",
            "data": json.dumps({
                "x": ["1月", "2月", "3月", "4月", "5月", "6月"],
                "y": [100, 120, 115, 130, 125, 140]
            }),
            "title": "月度GMV趋势",
            "filename": "test_line"
        })
        print(f"   状态: {result.status.value}")
        print(f"   结果: {result.text}")

    print("\n✅ ChartGeneratorTool 测试完成")


def test_report_generator():
    """测试报告生成工具"""
    print("\n" + "=" * 60)
    print("测试 ReportGeneratorTool")
    print("=" * 60)

    import json
    import tempfile

    from data_insight.tools.report_generator_tool import ReportGeneratorTool

    with tempfile.TemporaryDirectory() as tmpdir:
        report_tool = ReportGeneratorTool(output_dir=tmpdir)

        # 测试 Markdown 报告
        print("\n1. Markdown 报告:")
        result = report_tool.run({
            "report_type": "markdown",
            "title": "2026年6月华东区经营分析报告",
            "content": json.dumps({
                "sections": [
                    {"title": "📊 概况", "content": "华东区6月GMV为1.2亿，环比下降12%。"},
                    {"title": "🔍 归因分析", "content": "1. A门店：竞对影响\n2. B门店：促销透支"},
                    {"title": "📋 建议", "content": "1. 加强会员运营\n2. 控制促销节奏"}
                ]
            }),
            "filename": "test_report"
        })
        print(f"   状态: {result.status.value}")
        print(f"   结果: {result.text}")

    print("\n✅ ReportGeneratorTool 测试完成")


def test_memory_tool():
    """测试业务记忆工具"""
    print("\n" + "=" * 60)
    print("测试 MemoryTool")
    print("=" * 60)

    import tempfile

    from data_insight.tools.memory_tool import MemoryTool

    with tempfile.TemporaryDirectory() as tmpdir:
        memory_tool = MemoryTool(
            user_profiles_dir=tmpdir,
            conversation_dir=tmpdir
        )

        # 测试保存偏好
        print("\n1. 保存偏好:")
        result = memory_tool.save_preference("test_user", "focus_metrics", "GMV")
        print(f"   状态: {result.status.value}")
        print(f"   结果: {result.text}")

        # 测试记录查询
        print("\n2. 记录查询:")
        result = memory_tool.record_query(
            "test_user",
            "上个月华东区GMV怎么样",
            metrics="GMV",
            regions="华东"
        )
        print(f"   状态: {result.status.value}")
        print(f"   结果: {result.text}")

        # 测试获取画像
        print("\n3. 获取画像:")
        result = memory_tool.get_profile("test_user")
        print(f"   状态: {result.status.value}")
        print(f"   结果: {result.text}")

    print("\n✅ MemoryTool 测试完成")


if __name__ == "__main__":
    test_data_analyzer()
    test_chart_generator()
    test_report_generator()
    test_memory_tool()

    print("\n" + "=" * 60)
    print("✅ 所有工具测试完成！")
    print("=" * 60)
