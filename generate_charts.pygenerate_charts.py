"""
测试 chart_generator 工具是否能正常生成图表
"""

import os
import sys
from pathlib import Path

project_dir = Path(__file__).parent
sys.path.insert(0, str(project_dir))

from data_insight.tools.chart_generator_tool import ChartGeneratorTool

charts_dir = str(project_dir / "data_insight" / "output" / "charts")
tool = ChartGeneratorTool(output_dir=charts_dir)

print("📊 测试 chart_generator 工具\n")

# 测试 1：折线图 - 月度趋势
print("1️⃣  生成折线图（月度趋势）...")
result = tool.run({
    "chart_type": "line",
    "data": '{"x": ["1月", "2月", "3月", "4月", "5月", "6月", "7月"], "y": [190, 1509, 2025, 1835, 2142, 2301, 1792]}',
    "title": "月度 GMV 趋势（万元）",
    "x_label": "月份",
    "y_label": "GMV（万元）",
    "filename": "monthly_trend"
})
print(f"   {result.text}\n")

# 测试 2：柱状图 - 区域 GMV
print("2️⃣  生成柱状图（区域 GMV）...")
result = tool.run({
    "chart_type": "bar",
    "data": '{"labels": ["华南", "华东", "华北", "华西"], "values": [2987, 2959, 2943, 2906]}',
    "title": "各区域 GMV 对比（万元）",
    "x_label": "区域",
    "y_label": "GMV（万元）",
    "filename": "region_gmv"
})
print(f"   {result.text}\n")

# 测试 3：饼图 - 品类占比
print("3️⃣  生成饼图（品类占比）...")
result = tool.run({
    "chart_type": "pie",
    "data": '{"labels": ["电子产品", "服装", "家居", "美妆", "食品"], "values": [8411, 1494, 1079, 588, 224]}',
    "title": "品类 GMV 占比",
    "filename": "category_pie"
})
print(f"   {result.text}\n")

# 测试 4：柱状图 - Top 10 门店
print("4️⃣  生成柱状图（Top 10 门店）...")
result = tool.run({
    "chart_type": "bar",
    "data": '{"labels": ["成都旗舰店", "重庆标准店", "杭州社区店", "西安社区店", "宁波标准店", "济南旗舰店", "深圳社区店", "青岛标准店", "广州旗舰店", "苏州社区店"], "values": [589, 587, 586, 576, 562, 558, 555, 551, 548, 545]}',
    "title": "Top 10 门店 GMV 排名（万元）",
    "filename": "top10_stores"
})
print(f"   {result.text}\n")

# 测试 5：柱状图 - 渠道对比
print("5️⃣  生成柱状图（渠道对比）...")
result = tool.run({
    "chart_type": "bar",
    "data": '{"labels": ["线上", "线下"], "values": [5922, 5873]}',
    "title": "线上 vs 线下 GMV 对比（万元）",
    "filename": "channel_compare"
})
print(f"   {result.text}\n")

print(f"🎉 全部完成！图表保存在: {charts_dir}")
print("   打开报告文件即可看到图表。")
"""
测试 chart_generator 工具是否能正常生成图表
"""

import os
import sys
from pathlib import Path

project_dir = Path(__file__).parent
sys.path.insert(0, str(project_dir))

from data_insight.tools.chart_generator_tool import ChartGeneratorTool

charts_dir = str(project_dir / "data_insight" / "output" / "charts")
tool = ChartGeneratorTool(output_dir=charts_dir)

print("📊 测试 chart_generator 工具\n")

# 测试 1：折线图 - 月度趋势
print("1️⃣  生成折线图（月度趋势）...")
result = tool.run({
    "chart_type": "line",
    "data": '{"x": ["1月", "2月", "3月", "4月", "5月", "6月", "7月"], "y": [190, 1509, 2025, 1835, 2142, 2301, 1792]}',
    "title": "月度 GMV 趋势（万元）",
    "x_label": "月份",
    "y_label": "GMV（万元）",
    "filename": "monthly_trend"
})
print(f"   {result.text}\n")

# 测试 2：柱状图 - 区域 GMV
print("2️⃣  生成柱状图（区域 GMV）...")
result = tool.run({
    "chart_type": "bar",
    "data": '{"labels": ["华南", "华东", "华北", "华西"], "values": [2987, 2959, 2943, 2906]}',
    "title": "各区域 GMV 对比（万元）",
    "x_label": "区域",
    "y_label": "GMV（万元）",
    "filename": "region_gmv"
})
print(f"   {result.text}\n")

# 测试 3：饼图 - 品类占比
print("3️⃣  生成饼图（品类占比）...")
result = tool.run({
    "chart_type": "pie",
    "data": '{"labels": ["电子产品", "服装", "家居", "美妆", "食品"], "values": [8411, 1494, 1079, 588, 224]}',
    "title": "品类 GMV 占比",
    "filename": "category_pie"
})
print(f"   {result.text}\n")

# 测试 4：柱状图 - Top 10 门店
print("4️⃣  生成柱状图（Top 10 门店）...")
result = tool.run({
    "chart_type": "bar",
    "data": '{"labels": ["成都旗舰店", "重庆标准店", "杭州社区店", "西安社区店", "宁波标准店", "济南旗舰店", "深圳社区店", "青岛标准店", "广州旗舰店", "苏州社区店"], "values": [589, 587, 586, 576, 562, 558, 555, 551, 548, 545]}',
    "title": "Top 10 门店 GMV 排名（万元）",
    "filename": "top10_stores"
})
print(f"   {result.text}\n")

# 测试 5：柱状图 - 渠道对比
print("5️⃣  生成柱状图（渠道对比）...")
result = tool.run({
    "chart_type": "bar",
    "data": '{"labels": ["线上", "线下"], "values": [5922, 5873]}',
    "title": "线上 vs 线下 GMV 对比（万元）",
    "filename": "channel_compare"
})
print(f"   {result.text}\n")

print(f"🎉 全部完成！图表保存在: {charts_dir}")
print("   打开报告文件即可看到图表。")
