"""
由和风天气官方城市列表生成 data/city_mapping.json。

用法：
    1. 下载城市列表 CSV（含 ID 与中文城市名两列）：
       https://github.com/qwd/LocationList/blob/master/China-City-List-latest.csv
       备用：https://gitee.com/laia/city-mapping/raw/main/China-City-List-latest.csv
    2. 将 CSV 放到本文件同级目录，必要时修改下方 CSV_FILE
    3. 执行：python generate_city_mapping.py

不生成也不影响启动：插件会退回内置的北京 / 上海 / 临沂映射。
"""

import os
import json
import csv

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
OUTPUT_FILE = os.path.join(DATA_DIR, "city_mapping.json")

# 请将下载好的 CSV 文件放在当前目录，并修改路径
CSV_FILE = "China-City-List-latest.csv"  # 从下方链接下载


def parse_csv(file_path):
    mapping = {}
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # 跳过标题行
        for row in reader:
            # 根据实际 CSV 列调整：通常第1列是 ID，第2列是城市名（中文），第3列是拼音名（英文）
            if len(row) >= 3:
                city_id = row[0].strip()
                city_name_cn = row[1].strip()
                # 去除括号内的别名
                city_name_cn = city_name_cn.split("（")[0].split("(")[0]
                mapping[city_name_cn] = city_id
    return mapping


def main():
    if not os.path.exists(CSV_FILE):
        print(f"错误：找不到 CSV 文件 {CSV_FILE}")
        print("请手动下载 CSV 文件：")
        print("https://github.com/qwd/LocationList/blob/master/China-City-List-latest.csv")
        print("或备用链接：https://gitee.com/laia/city-mapping/raw/main/China-City-List-latest.csv")
        return
    mapping = parse_csv(CSV_FILE)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"已生成 {OUTPUT_FILE}，共 {len(mapping)} 个城市。")


if __name__ == "__main__":
    main()
