# 软著材料编写说明

本目录保存“农田氮素遥感监测与反演平台 V1.0”的软著文字材料。

当前以 Markdown 为正式编辑格式。文档不嵌入图片，只在需要配图的位置写明建议图片路径、插入位置和图注。可以把 Markdown 继续编辑，也可以再转入 Word。

## 文件清单

| 文件 | 用途 |
|---|---|
| `01_软件说明书.md` | 软件定位、功能范围、技术特点、运行环境和能力边界 |
| `02_用户操作手册.md` | 安装启动、项目、导入、查看、预处理、反演和结果导出 |
| `03_设计说明书.md` | 架构、数据模型、算法、接口、安全与测试设计 |
| `04_源程序拼版文档_前30页后30页.md` | 每页 50 行的源程序提交版材料 |
| `05_软著申请书底稿.md` | 登记申请表可填写内容与待补信息 |

Few-shot 校正的输入格式、算法、API 和边界见 `../FewShot实测校正说明.md`。

## 主要配图文件

下表路径相对于本目录。若从项目根目录查找，对应路径为 `docs/screenshots/...` 或 `docs/examples/...`。

| 编号 | 文件 | 建议图注 |
|---|---|---|
| 图 1 | `../screenshots/11_planetscope_home.png` | 软件首页与 PlanetScope 监测项目 |
| 图 2 | `../screenshots/12_planetscope_import.png` | 自动识别影像元数据、来源和云量冲突 |
| 图 3 | `../screenshots/13_planetscope_viewer_spectrum.png` | PlanetScope 影像与像元光谱查询 |
| 图 4 | `../screenshots/14_planetscope_preprocess.png` | 数据预处理参数、进度和质量检查 |
| 图 5 | `../screenshots/15_planetscope_inversion.png` | 模型适用性预检和 DNN 模型反演 |
| 图 6 | `../screenshots/16_planetscope_results.png` | 氮素分级专题图、统计和结果追溯 |
| 图 7 | `../examples/PlanetScope恒福菜场_流程演示专题图.png` | PlanetScope 案例导出的专题图 |
| 图 8 | `../screenshots/17_fewshot_modal.png` | 基于实测样点的 Few-shot 微调界面 |

PlanetScope 影像由用户提供，仅用于本地展示和当前软著材料。公开源码仓库时，应保留 `.gitignore` 对原始影像、派生影像和案例截图的排除规则。

## 事实依据

- `docs/软件功能事实表.md`
- `docs/PlanetScope真实案例事实.md`
- `docs/V1.0增强验收记录.md`
- `README.md`
- 当前源代码、内置模型元数据和自动化测试

## 正式提交前需要用户确认

1. 申请人或著作权人的名称、证件和联系方式。
2. 开发方式、权利取得方式、开发完成日期、发表状态和首次发表日期。
3. 最终使用的软件全称和是否保留软件简称。
4. PlanetScope 数据是否允许用于软著截图和随材料提交。

