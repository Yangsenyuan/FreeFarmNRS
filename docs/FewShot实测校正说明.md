# Few-shot 实测校正说明

## 定义

本平台的 Few-shot 指“少样本后校准”，不是重新训练父模型。

用户先用模型库中的基础模型完成当前影像反演，再提供少量带实测 Nmass 的样点。平台从当前结果栅格读取这些像元的基础预测值，学习一个偏置或收缩线性校正，并保存为派生模型。

父模型文件保持不变。派生模型只记录父模型引用、校正系数、样点和评估信息。

## 输入格式

像素坐标 CSV：

```text
sample_id,col,row,nmass
s1,120,80,23.4
s2,240,96,25.1
s3,360,144,21.8
s4,420,210,26.0
s5,510,260,27.3
```

像素坐标示例文件：`docs/examples/few_shot_samples_template.csv`。

GPS 坐标 CSV：

```text
sample_id,lon,lat,nmass
s1,114.17650,23.01480,23.4
s2,114.17800,23.01420,25.1
s3,114.17920,23.01360,21.8
```

经纬度示例文件：`docs/examples/few_shot_samples_lonlat_template.csv`。

经纬度按 WGS84 解释，并根据当前结果的 CRS 和仿射变换转换为结果行列号。

字段说明：

| 字段 | 说明 |
|---|---|
| `sample_id` | 可选样点标识，重复时自动追加序号 |
| `col` | 可选，预处理结果栅格列号，从 0 开始 |
| `row` | 可选，预处理结果栅格行号，从 0 开始 |
| `lon` | 可选，WGS84 经度 |
| `lat` | 可选，WGS84 纬度 |
| `nmass` | 实测 Nmass，必须为有限正数 |

实测单位必须与当前结果单位一致。平台不进行 `mg/g` 与 `g/kg` 的隐式换算。

## 样点要求

- 位于当前结果影像范围内。
- 位于有效植被掩膜内。
- 没有 NaN/Inf。
- 同一 `(col,row)` 不重复。
- 建议来自空间上相互独立的田块或位置。
- 建议至少 5 个有效样点。

## 校正策略

| 有效样点数 | 方法 | 说明 |
|---:|---|---|
| 1–2 | 偏置校正 | 斜率固定为 1，只调整整体偏差 |
| 3–4 | 收缩线性校正 | 拟合斜率向 1 收缩，降低小样本过拟合 |
| 5 及以上 | 后校准 | 拟合斜率与截距，并向 1 保守收缩 |

公式：

```text
calibrated = slope × base_prediction + intercept
```

拟合斜率被限制在 `0.25–4.0`。出现限制时会在校准结果中记录。

## 评估指标

平台输出：

- 校正前 MAE、RMSE、偏差和 R²。
- 校正后样本内 MAE、RMSE、偏差和 R²。
- 留一验证 MAE、RMSE、偏差和 R²。
- 拟合斜率、截距、原始斜率和收缩系数。
- 被拒绝样点及原因。

样本内指标会因少量样点而偏乐观。留一指标用于辅助判断稳定性，但仍不能替代独立田间验证。

## 派生模型

派生模型保存在 `data/models/<model_id>/meta.json`。

关键字段：

```text
model_kind = few_shot_calibrated
parent_model_id
parent_file_sha256
few_shot.mode
few_shot.slope
few_shot.intercept
few_shot.sample_count
few_shot.sample_fingerprint
few_shot.samples
few_shot.after_in_sample
few_shot.leave_one_out
```

派生模型不包含父模型二进制副本。父模型不存在或文件缺失时，派生模型标记为不可用。被派生模型引用的自定义父模型不能直接删除。

## 结果保存

应用校正前，平台把当前结果摘要、Nmass 数组、GeoTIFF 和预览复制到：

```text
results/calibration_sources/<few_shot_model_id>/
```

随后派生模型重新反演当前影像。新的 `latest.json` 记录：

- 派生模型 ID。
- 父模型 ID、名称和哈希。
- 校正模式、斜率和截距。
- 样点数量、单位、样点指纹。
- 校正前后及留一指标。
- 校正前结果快照路径。

## API

### 下载 CSV 模板

```text
GET /api/projects/{pid}/images/{iid}/few-shot/template.csv
```

经纬度模板增加查询参数 `?coordinate=lonlat`。

### 预览校正

```text
POST /api/projects/{pid}/images/{iid}/few-shot/preview
```

请求：

```json
{
  "unit": "mg/g",
  "samples": [
    {"sample_id": "s1", "col": 120, "row": 80, "nmass": 23.4},
    {"sample_id": "s2", "col": 240, "row": 96, "nmass": 25.1},
    {"sample_id": "s3", "col": 360, "row": 144, "nmass": 21.8}
  ]
}
```

### 应用校正

```text
POST /api/projects/{pid}/images/{iid}/few-shot/apply
```

请求在预览字段基础上增加：

```json
{
  "name": "恒福菜场实测校正模型",
  "force": false
}
```

返回后台任务 ID、派生模型和预览校正信息。任务完成后，当前影像结果切换为该派生模型的校正结果。

## 边界

- 不更新父模型的内部权重。
- 不替代大样本独立训练集。
- 不允许跨单位直接校正。
- 不将合成示例模型的校正结果表述为真实田间精度。
- 结果页同时展示校正前、样本内和留一指标，避免只显示样本内改善。
