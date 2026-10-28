import re

import pandas as pd

def base_model(x) -> str:
    """사이즈 옵션을 뗀 모델명. 예) 'BFUSW035 730 001 (XL)' -> 'BFUSW035 730 001', '198018-52' -> '198018'"""
    x = re.sub(r"\s*\([^)]*\)\s*$", "", str(x).strip())
    return re.sub(r"-\d+$", "", x).strip()


REQUIRED = ["주문번호", "브랜드", "카테고리", "모델명", "수량", "최종판매가", "수수료(%)", "원가총액", "수익원(실배송비)"]


def load_sales(file) -> pd.DataFrame:
    """ERP 판매 엑셀 1개를 읽어 정제된 DataFrame으로 반환한다."""
    raw = pd.read_excel(file, header=None)
    # '주문번호'가 들어 있는 행을 헤더로 사용 (첫 줄이 비어 있는 양식 대응)
    header_row = None
    for i in range(min(10, len(raw))):
        if "주문번호" in raw.iloc[i].astype(str).values:
            header_row = i
            break
    if header_row is None:
        raise ValueError("'주문번호' 컬럼을 찾을 수 없습니다. 올바른 판매 파일인지 확인해 주세요.")

    df = raw.iloc[header_row + 1:].copy()
    df.columns = raw.iloc[header_row].astype(str).str.strip().values
    df = df.reset_index(drop=True)

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼이 없습니다: {', '.join(missing)}")

    df = df[df["주문번호"].notna()].copy()
    df["주문번호"] = df["주문번호"].astype(str).str.strip()

    cost_cols = ["수량", "최종판매가", "원가총액", "수익원(실배송비)"] + (["출고원가"] if "출고원가" in df.columns else [])
    for c in cost_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # 출고원가가 0인 행 = 미출고(주문취소 포함) → 집계 제외
    cost_basis = df["출고원가"] if "출고원가" in df.columns else df["원가총액"]
    excluded = int((cost_basis == 0).sum())
    df = df[cost_basis != 0].copy()

    df["수수료율"] = pd.to_numeric(
        df["수수료(%)"].astype(str).str.replace("%", "", regex=False), errors="coerce"
    )
    df["모델명"] = df["모델명"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    df["옵션"] = df["모델명"]                      # 사이즈 포함 원본
    df["모델명"] = df["옵션"].map(base_model)      # 사이즈 합친 모델 (랭킹·이미지 기준)

    # 출고날짜: '0000-00-00'(당일 출고, ERP 미반영)은 NaT로 두고 app에서 오늘 날짜로 채운다
    if "출고날짜" in df.columns:
        df["출고일"] = pd.to_datetime(df["출고날짜"].astype(str).str.strip(), format="mixed", errors="coerce")
    else:
        df["출고일"] = pd.NaT

    # 주문취소건 제외 (비고에 '취소'가 적힌 행)
    if "비고" in df.columns:
        cancel = df["비고"].astype(str).str.contains("취소", na=False)
        df = df[~cancel]

    df.attrs["excluded_zero_cost"] = excluded
    return df


def combine(frames) -> pd.DataFrame:
    """여러 파일을 합친다. 같은 파일 안의 행은 전부 살리고, 앞선 파일과 겹치는 주문만 제거한다."""
    out, seen = [], set()
    for f in frames:
        key_cols = [c for c in ["주문번호", "옵션", "차수"] if c in f.columns]
        keys = f[key_cols].astype(str).agg("|".join, axis=1)
        out.append(f[~keys.isin(seen)])
        seen |= set(keys)
    return pd.concat(out, ignore_index=True)


def _agg(df: pd.DataFrame, keys) -> pd.DataFrame:
    g = df.groupby(keys, dropna=False).agg(
        주문건수=("주문번호", "nunique"),
        수량=("수량", "sum"),
        매출=("최종판매가", "sum"),
        원가=("원가총액", "sum"),
        이익=("수익원(실배송비)", "sum"),
    ).reset_index()
    g["수익률"] = (g["이익"] / g["매출"].where(g["매출"] != 0) * 100).round(1)
    g["평균판매가"] = (g["매출"] / g["수량"].where(g["수량"] != 0)).round(0)
    return g


def by_brand(df):
    g = _agg(df, ["브랜드"])
    total = g["매출"].sum()
    g["매출비중"] = (g["매출"] / total * 100).round(1) if total else 0
    return g.sort_values("매출", ascending=False)


def by_category(df):
    return _agg(df, ["브랜드", "카테고리"]).sort_values("매출", ascending=False)


def by_fee(df):
    return _agg(df, ["수수료율"]).sort_values("수수료율")


def by_product(df):
    keys = ["브랜드", "모델명", "카테고리"]
    if "상품명" in df.columns:
        keys = ["브랜드", "모델명", "상품명", "카테고리"]
    g = _agg(df, keys)
    # 매출 가중 평균 수수료율
    w = df.assign(_w=df["수수료율"] * df["최종판매가"]).groupby(keys, dropna=False)["_w"].sum().reset_index()
    g = g.merge(w, on=keys)
    g["평균수수료율"] = (g["_w"] / g["매출"].where(g["매출"] != 0)).round(1)
    return g.drop(columns="_w")


def totals(df):
    sales = df["최종판매가"].sum()
    profit = df["수익원(실배송비)"].sum()
    qty = df["수량"].sum()
    return {
        "매출": sales,
        "이익": profit,
        "수익률": profit / sales * 100 if sales else 0,
        "수량": qty,
        "주문건수": df["주문번호"].nunique(),
        "평균판매가": sales / qty if qty else 0,
    }


def window(df: pd.DataFrame, ref, months: int = 1, years_back: int = 0, forward: bool = False) -> tuple:
    """기간 구간의 데이터를 잘라 (데이터, 시작일, 종료일)로 반환한다.
    forward=False: 기준일(ref)을 끝으로 거슬러 올라가는 최근 N개월
    forward=True : years_back년 전 기준일부터 앞으로 N개월 (예: 작년 이맘때부터 겨울 시즌)
    """
    ref = pd.Timestamp(ref).normalize() - pd.DateOffset(years=years_back)
    if forward:
        start, end = ref, ref + pd.DateOffset(months=months) - pd.Timedelta(days=1)
    else:
        start, end = ref - pd.DateOffset(months=months) + pd.Timedelta(days=1), ref
    sub = df[(df["출고일"] >= start) & (df["출고일"] <= end)]
    return sub, start, end


def norm_key(x) -> str:
    """모델명/라인명 매칭용 키: 공백 제거 + 대문자."""
    return str(x).replace(" ", "").strip().upper()


def load_image_map(file) -> dict:
    """모델명(라인명) -> 이미지 URL 매핑 파일(csv/xlsx)을 dict로 읽는다. 키는 norm_key로 정규화."""
    name = str(getattr(file, "name", file)).lower()
    m = pd.read_csv(file, dtype=str) if name.endswith(".csv") else pd.read_excel(file, dtype=str)
    m.columns = [str(c).strip() for c in m.columns]
    key_col = next((c for c in ["모델명", "라인명"] if c in m.columns), None)
    url_col = next((c for c in ["이미지 링크", "이미지링크", "이미지URL", "이미지", "image", "url", "img"] if c in m.columns), None)
    if key_col is None or url_col is None:
        raise ValueError("매핑 파일에 '모델명(또는 라인명)'과 '이미지 링크(또는 이미지URL)' 컬럼이 필요합니다.")
    m = m.dropna(subset=[key_col, url_col])
    m["_k"] = m[key_col].map(norm_key)
    m["_u"] = m[url_col].astype(str).str.strip().str.replace(" ", "%20", regex=False)
    m = m.drop_duplicates(subset="_k", keep="first")
    return dict(zip(m["_k"], m["_u"]))
