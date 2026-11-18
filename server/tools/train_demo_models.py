"""训练内置示例模型（合成数据，仅用于功能演示）。

运行：
    .venv\\Scripts\\python.exe -m server.tools.train_demo_models

输出到 server/builtin_models/：
    vi_ndre_general.json
    plsr_nmass_demo.pkl + .json
    rf_nmass_demo.pkl + .json
    svr_nmass_demo.pkl + .json
    dnn_nmass_demo.pkl + .json
    dnn_multispectral_demo.pkl + .json
"""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from scipy.signal import savgol_filter
from sklearn.cross_decomposition import PLSRegression
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

from server.core.config import BUILTIN_MODELS_DIR
from server.services.spectral_sim import simulate_spectra, wavelengths_default

DEMO_NOTE = (
    "示例模型：基于合成冠层光谱数据训练，仅用于平台功能演示。"
    "正式科研应用请导入基于实测氮素数据训练的模型。"
)

WL = wavelengths_default()


def make_dataset(n: int = 2400, seed: int = 42):
    rng = np.random.default_rng(seed)
    nmass = rng.uniform(15.0, 35.0, n).astype(np.float32)
    # 冠层覆盖度差异（模拟裸土混合、长势不均）
    veg = rng.uniform(0.45, 1.0, n).astype(np.float32)
    spectra = simulate_spectra(nmass, veg, WL, noise=0.004, rng=rng)
    return spectra.reshape(n, -1), nmass


def metrics(y_true, y_pred):
    return {
        "r2": round(float(r2_score(y_true, y_pred)), 4),
        "rmse": round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 4),
    }


def write_meta(name: str, data: dict):
    BUILTIN_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(BUILTIN_MODELS_DIR / name, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_train_validation_test(X, y, seed):
    """80/10/10 split; the test partition is held out from fitting."""
    X_fit, X_test, y_fit, y_test = train_test_split(
        X, y, test_size=0.10, random_state=seed, shuffle=True
    )
    X_train, X_validation, y_train, y_validation = train_test_split(
        X_fit, y_fit, test_size=1.0 / 9.0, random_state=seed + 1, shuffle=True
    )
    return X_train, X_validation, X_test, y_train, y_validation, y_test


def train_mlp_example(
    X,
    y,
    wavelengths,
    model_id: str,
    name: str,
    seed: int,
    hidden_layers,
):
    X_train, X_validation, X_test, y_train, y_validation, y_test = (
        split_train_validation_test(X, y, seed)
    )
    model = TransformedTargetRegressor(
        regressor=Pipeline(
            [
                ("features", StandardScaler()),
                (
                    "mlp",
                    MLPRegressor(
                        hidden_layer_sizes=tuple(int(v) for v in hidden_layers),
                        activation="relu",
                        solver="adam",
                        alpha=1e-4,
                        batch_size=128,
                        learning_rate="adaptive",
                        learning_rate_init=1e-3,
                        max_iter=900,
                        shuffle=True,
                        random_state=seed,
                        tol=1e-5,
                        n_iter_no_change=35,
                        early_stopping=False,
                    ),
                ),
            ]
        ),
        transformer=StandardScaler(),
    )
    model.fit(X_train, y_train)
    train_metrics = metrics(y_train, model.predict(X_train))
    validation_metrics = metrics(y_validation, model.predict(X_validation))
    test_metrics = metrics(y_test, model.predict(X_test))
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    meta = {
        "id": model_id,
        "name": name,
        "type": "ml",
        "algorithm": "DNN / MLP",
        "builtin": True,
        "demo": True,
        "synthetic": True,
        "version": "1.0",
        "unit": "mg/g",
        "crops": ["玉米", "小麦"] if len(wavelengths) == 5 else ["玉米", "小麦", "水稻"],
        "sensors": (
            ["五波段多光谱（475/560/668/717/840 nm）"]
            if len(wavelengths) == 5
            else ["高光谱", "多光谱（需重采样到 51 个训练波段）"]
        ),
        "training_data": (
            "合成冠层光谱训练的示例数据；不是田间实测数据，不能用于生产精度评价。"
        ),
        "description": (
            "合成数据训练的示例模型：输入标准化光谱后，经两层或多层全连接神经网络回归 Nmass。"
            "仅用于平台功能演示和流程验收，不表示对真实田块的预测精度。"
        ),
        "input": {
            "type": "spectra",
            "wavelengths": [round(float(v), 3) for v in wavelengths],
        },
        "thresholds": {"low": 22.0, "high": 28.0},
        "tags": ["合成数据示例", "DNN", "MLP", "Nmass"],
        "dataset": {
            "synthetic": True,
            "generator": "server/services/spectral_sim.py",
            "sample_count": int(len(y)),
            "feature_count": int(X.shape[1]),
            "seed": int(seed),
            "split_method": "80% train / 10% validation / 10% held-out test",
            "train_count": int(len(y_train)),
            "validation_count": int(len(y_validation)),
            "test_count": int(len(y_test)),
        },
        "structure": {
            "input_dim": int(X.shape[1]),
            "hidden_layers": [int(v) for v in hidden_layers],
            "output_dim": 1,
            "activation": "relu",
            "feature_scaler": "StandardScaler",
            "target_scaler": "StandardScaler",
            "optimizer": "adam",
            "batch_size": 128,
            "max_iter": 900,
        },
        "metrics": {
            "metric_type": "independent_test",
            "test_set": "held-out 10%，未参与训练或验证",
            "train": train_metrics,
            "validation": validation_metrics,
            "test": test_metrics,
        },
        "metric_type": "independent_test",
        "test_metrics": test_metrics,
        "r2": test_metrics["r2"],
        "rmse": test_metrics["rmse"],
        "limitations": [
            "训练数据由简化光谱模拟器合成，不代表真实传感器或田间实测分布。",
            "模型指标仅反映合成数据独立测试集，不是当前影像精度验证结果。",
            "模型只接受所列训练中心波长；其他波段会先线性重采样。",
            "正式科研与生产应用必须导入基于实测 Nmass 数据训练的模型。",
        ],
        "training_environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "trained_at": timestamp,
    }
    filename = f"{model_id}.pkl"
    target = BUILTIN_MODELS_DIR / filename
    joblib.dump(model, target, compress=3)
    meta["file"] = filename
    meta["file_sha256"] = sha256_file(target)
    meta["file_size_bytes"] = int(target.stat().st_size)
    write_meta(f"{model_id}.json", meta)
    return meta, {
        "train": train_metrics,
        "validation": validation_metrics,
        "test": test_metrics,
    }


def base_meta(model_id: str, name: str, algorithm: str) -> dict:
    return {
        "id": model_id,
        "name": name,
        "type": "ml",
        "algorithm": algorithm,
        "builtin": True,
        "demo": True,
        "version": "1.0",
        "unit": "mg/g",
        "crops": ["玉米", "小麦", "水稻"],
        "sensors": ["多光谱", "高光谱"],
        "training_data": "合成冠层光谱数据集（2400 条，400–900 nm）",
        "description": DEMO_NOTE,
        "input": {"type": "spectra", "wavelengths": [float(v) for v in WL]},
        "thresholds": {"low": 22.0, "high": 28.0},
        "tags": ["植被氮素", "Nmass", "光谱反演"],
    }


def main():
    X, y = make_dataset()
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=42)

    # 1) 植被指数经验模型（NDRE + NDVI，兼顾氮素信号与冠层覆盖度）
    def vi_features(flat: np.ndarray) -> np.ndarray:
        spec = flat.reshape(-1, len(WL))
        nir = spec[:, int(np.argmin(np.abs(WL - 840)))]
        re = spec[:, int(np.argmin(np.abs(WL - 730)))]
        red = spec[:, int(np.argmin(np.abs(WL - 670)))]
        ndre = (nir - re) / (nir + re + 1e-9)
        ndvi = (nir - red) / (nir + red + 1e-9)
        return np.column_stack([ndre, ndvi])

    lr = LinearRegression()
    lr.fit(vi_features(X_train), y_train)
    vi_pred = lr.predict(vi_features(X_test))
    vi_metrics = metrics(y_test, vi_pred)
    vi_meta = {
        "id": "vi_ndre_general",
        "name": "通用 Nmass-NDRE/NDVI 经验模型",
        "type": "vi",
        "algorithm": "植被指数回归",
        "builtin": True,
        "demo": True,
        "version": "1.0",
        "unit": "mg/g",
        "crops": ["玉米", "小麦", "水稻", "大豆"],
        "sensors": ["多光谱", "高光谱"],
        "training_data": "合成冠层光谱数据集（2400 条，400–900 nm）",
        "r2": vi_metrics["r2"],
        "rmse": vi_metrics["rmse"],
        "description": DEMO_NOTE
        + " 该模型为线性经验关系，可解释性强，适合快速预览与趋势判断。",
        "input": {
            "type": "vi",
            "features": [
                {"index": "NDRE", "bands": {"nir": 840.0, "red_edge": 730.0}},
                {"index": "NDVI", "bands": {"nir": 840.0, "red": 670.0}},
            ],
            "intercept": float(lr.intercept_),
            "coef": [float(v) for v in lr.coef_],
        },
        "thresholds": {"low": 22.0, "high": 28.0},
        "tags": ["NDRE", "经验模型", "零样本"],
    }
    write_meta("vi_ndre_general.json", vi_meta)
    print("VI 模型：", vi_metrics)

    # 2) PLSR
    plsr = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("pls", PLSRegression(n_components=10, scale=False)),
        ]
    )
    plsr.fit(X_train, y_train)
    plsr_pred = plsr.predict(X_test).ravel()
    plsr_metrics = metrics(y_test, plsr_pred)
    joblib.dump(plsr, BUILTIN_MODELS_DIR / "plsr_nmass_demo.pkl")
    plsr_meta = base_meta("plsr_nmass_demo", "玉米 Nmass-PLSR 模型", "PLSR")
    plsr_meta.update(plsr_metrics)
    plsr_meta["file"] = "plsr_nmass_demo.pkl"
    plsr_meta["description"] = (
        DEMO_NOTE + " PLSR 对全谱段降维回归，兼顾精度与可解释性，适用于高光谱/多光谱数据。"
    )
    write_meta("plsr_nmass_demo.json", plsr_meta)
    print("PLSR：", plsr_metrics)

    # 3) 随机森林
    rf = RandomForestRegressor(
        n_estimators=260, max_depth=14, min_samples_leaf=3, random_state=42, n_jobs=-1
    )
    rf.fit(X_train, y_train)
    rf_pred = rf.predict(X_test)
    rf_metrics = metrics(y_test, rf_pred)
    joblib.dump(rf, BUILTIN_MODELS_DIR / "rf_nmass_demo.pkl")
    rf_meta = base_meta("rf_nmass_demo", "小麦 Nmass-随机森林模型", "随机森林")
    rf_meta.update(rf_metrics)
    rf_meta["file"] = "rf_nmass_demo.pkl"
    rf_meta["description"] = DEMO_NOTE + " 随机森林可捕捉非线性关系，适合长势差异较大的田块。"
    write_meta("rf_nmass_demo.json", rf_meta)
    print("RF：", rf_metrics)

    # 4) SVR
    svr = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("svr", SVR(kernel="rbf", C=120.0, epsilon=0.4, gamma="scale")),
        ]
    )
    svr.fit(X_train, y_train)
    svr_pred = svr.predict(X_test)
    svr_metrics = metrics(y_test, svr_pred)
    joblib.dump(svr, BUILTIN_MODELS_DIR / "svr_nmass_demo.pkl")
    svr_meta = base_meta("svr_nmass_demo", "通用 Nmass-SVR 模型", "SVR")
    svr_meta.update(svr_metrics)
    svr_meta["file"] = "svr_nmass_demo.pkl"
    svr_meta["description"] = DEMO_NOTE + " 支持向量回归在小样本、高维光谱条件下表现稳健。"
    write_meta("svr_nmass_demo.json", svr_meta)
    print("SVR：", svr_metrics)

    # 5) 五波段多光谱专用模型（输入与常见多光谱传感器一致，经 SG 平滑）
    WL5 = np.array([475.0, 560.0, 668.0, 717.0, 840.0])
    rng5 = np.random.default_rng(2026)
    n5 = 4000
    nmass5 = rng5.uniform(15.0, 35.0, n5).astype(np.float32)
    veg5 = rng5.uniform(0.55, 1.0, n5).astype(np.float32)
    spec5 = simulate_spectra(nmass5, veg5, WL5, noise=0.004, rng=rng5).reshape(n5, len(WL5))
    spec5 = savgol_filter(spec5, 5, 2, axis=1)
    X5_train, X5_test, y5_train, y5_test = train_test_split(
        spec5, nmass5, test_size=0.25, random_state=42
    )
    rf5 = RandomForestRegressor(
        n_estimators=300, max_depth=14, min_samples_leaf=2, random_state=42, n_jobs=-1
    )
    rf5.fit(X5_train, y5_train)
    rf5_metrics = metrics(y5_test, rf5.predict(X5_test))
    joblib.dump(rf5, BUILTIN_MODELS_DIR / "rf_multispectral_demo.pkl")
    rf5_meta = {
        "id": "rf_multispectral_demo",
        "name": "多光谱 Nmass-RF 模型",
        "type": "ml",
        "algorithm": "随机森林",
        "builtin": True,
        "demo": True,
        "version": "1.0",
        "unit": "mg/g",
        "crops": ["玉米", "小麦"],
        "sensors": ["五波段多光谱（475/560/668/717/840 nm）"],
        "training_data": "合成多光谱冠层光谱（4000 条，5 波段，Savitzky-Golay 平滑）",
        "r2": rf5_metrics["r2"],
        "rmse": rf5_metrics["rmse"],
        "description": DEMO_NOTE
        + " 针对蓝、绿、红、红边、近红外五波段多光谱传感器训练；随机森林反演速度快，适合大影像与批量处理。",
        "input": {"type": "spectra", "wavelengths": [float(v) for v in WL5]},
        "thresholds": {"low": 22.0, "high": 28.0},
        "tags": ["多光谱", "随机森林", "零样本"],
    }
    rf5_meta.update(rf5_metrics)
    rf5_meta["file"] = "rf_multispectral_demo.pkl"
    write_meta("rf_multispectral_demo.json", rf5_meta)
    print("多光谱 RF：", rf5_metrics)

    # 6) 51 波段全谱 MLP/DNN 示例模型
    X_dnn, y_dnn = make_dataset(n=7200, seed=3101)
    dnn_meta, dnn_metrics = train_mlp_example(
        X_dnn,
        y_dnn,
        WL,
        model_id="dnn_nmass_demo",
        name="全谱 51 波段 Nmass-DNN 示例模型",
        seed=3101,
        hidden_layers=(128, 64),
    )
    print("51波段 DNN：", dnn_metrics, "file:", dnn_meta["file_size_bytes"], "bytes")

    # 7) 五波段 MLP/DNN 示例模型
    rng_dnn5 = np.random.default_rng(3102)
    n_dnn5 = 7200
    y_dnn5 = rng_dnn5.uniform(15.0, 35.0, n_dnn5).astype(np.float32)
    veg_dnn5 = rng_dnn5.uniform(0.45, 1.0, n_dnn5).astype(np.float32)
    X_dnn5 = simulate_spectra(
        y_dnn5, veg_dnn5, WL5, noise=0.004, rng=rng_dnn5
    ).reshape(n_dnn5, len(WL5))
    X_dnn5 = savgol_filter(X_dnn5, 5, 2, axis=1).astype(np.float32)
    dnn5_meta, dnn5_metrics = train_mlp_example(
        X_dnn5,
        y_dnn5,
        WL5,
        model_id="dnn_multispectral_demo",
        name="五波段 Nmass-DNN 示例模型",
        seed=3102,
        hidden_layers=(64, 32),
    )
    print("五波段 DNN：", dnn5_metrics, "file:", dnn5_meta["file_size_bytes"], "bytes")

    print(f"模型已写入：{BUILTIN_MODELS_DIR}")


if __name__ == "__main__":
    main()
