import base64
from pathlib import Path

import pandas as pd
import streamlit as st

import utils

st.set_page_config(page_title="지그재그 브랜드 판매 대시보드", page_icon="📊", layout="wide")

IMG_DIR = Path(__file__).parent / "images"  # images/5286137.jpg 처럼 모델명으로 저장
DATA_DIR = Path(__file__).parent / "data"   # 여기에 판매 엑셀을 넣어두면 업로드 없이 자동으로 불러옴


# ---------------- 이미지 ----------------
def resolve_image(model: str, mapping: dict):
    """URL 또는 로컬 파일 경로를 반환 (없으면 None). 우선순위: 매핑 파일 > images 폴더."""
    k = utils.norm_key(model)
    if k in mapping:
        return mapping[k]
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = IMG_DIR / f"{model}.{ext}"
        if p.exists():
            return str(p)
    return None


@st.cache_data(show_spinner=False)
def to_table_src(src):
    """표(ImageColumn)용: URL은 그대로, 로컬 파일은 data URI로 변환. 이미지가 없으면 None."""
    if not isinstance(src, str) or not src:
        return None
    if src.startswith("http"):
        return src
    p = Path(src)
    mime = "image/png" if p.suffix == ".png" else "image/webp" if p.suffix == ".webp" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


# ---------------- 데이터 업로드 ----------------
st.sidebar.markdown("## 📂 데이터")
files = st.sidebar.file_uploader(
    "판매 엑셀 업로드 (브랜드·기간별 여러 개 가능)", type=["xlsx"], accept_multiple_files=True
)
map_file = st.sidebar.file_uploader(
    "이미지 매핑 파일 (라인명/모델명 + 이미지 링크)", type=["csv", "xlsx"],
    help="저장소에 image_map.xlsx를 같이 올려두면 자동으로 불러옵니다.",
)

auto_files = []
if DATA_DIR.exists():
    auto_files = sorted(p for p in DATA_DIR.glob("*.xlsx") if not p.name.startswith("~$"))
sources = list(files) if files else auto_files

if not sources:
    st.title("📊 지그재그 브랜드 판매 대시보드")
    st.info("왼쪽에서 ERP 판매 엑셀(sales_revenue...xlsx)을 업로드하면 대시보드가 만들어집니다. "
            "작년 동기간 비교를 보려면 작년 파일도 함께 올려주세요.")
    st.stop()
if not files:
    st.sidebar.success(f"저장소 data 폴더의 파일 {len(auto_files)}개를 자동으로 불러왔어요. "
                       "다른 파일을 올리면 그 파일로 대체됩니다.")

frames = []
for f in sources:
    try:
        frames.append(utils.load_sales(f))
    except Exception as e:
        st.sidebar.error(f"{f.name}: {e}")
if not frames:
    st.stop()

@st.cache_data(show_spinner=False)
def cached_default_map(path: str):
    return utils.load_image_map(path)


image_map = {}
default_map = Path(__file__).parent / "image_map.xlsx"
try:
    if map_file is not None:
        image_map = utils.load_image_map(map_file)
    elif default_map.exists():
        image_map = cached_default_map(str(default_map))
except Exception as e:
    st.sidebar.error(f"이미지 매핑 파일: {e}")

excluded_total = sum(f.attrs.get("excluded_zero_cost", 0) for f in frames)
data = utils.combine(frames)

# 출고날짜 0000-00-00 = 당일 출고(ERP 미반영) → 오늘 날짜로 처리
today = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None).normalize()
data["출고일"] = data["출고일"].fillna(today)

# ---------------- 필터 ----------------
st.sidebar.markdown("## 🔎 필터")
malls = sorted(data["쇼핑몰"].dropna().unique()) if "쇼핑몰" in data.columns else []
if malls:
    default_malls = [m for m in malls if "지그재그" in m] or malls
    sel_malls = st.sidebar.multiselect("쇼핑몰", malls, default=default_malls)
    data = data[data["쇼핑몰"].isin(sel_malls)]
brands = sorted(data["브랜드"].dropna().unique())
TARGET_BRANDS = ["판도라", "스와로브스키", "AMI", "비비안웨스트우드"]
default_brands = [b for b in TARGET_BRANDS if b in brands] or brands
sel_brands = st.sidebar.multiselect("브랜드", brands, default=default_brands)
cats = sorted(data["카테고리"].dropna().unique())
sel_cats = st.sidebar.multiselect("카테고리", cats, default=cats)
min_qty = st.sidebar.number_input("상품별 최소 판매수량", min_value=0, value=0, step=1)
ref_date = st.sidebar.date_input("기준일", value=today.date())

df = data[data["브랜드"].isin(sel_brands) & data["카테고리"].isin(sel_cats)]
if df.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다.")
    st.stop()

st.sidebar.caption("수익률 기준: 실배송비 / 출고일 0000-00-00은 오늘 출고로 집계")
st.sidebar.caption(f"출고원가 0(미출고·주문취소) 제외: {excluded_total:,}건")

# ---------------- KPI ----------------
st.title("📊 지그재그 브랜드 판매 요약")
st.caption(f"전체 업로드 데이터 {len(df):,}건 · {df['출고일'].min():%Y-%m-%d} ~ {df['출고일'].max():%Y-%m-%d} · "
           f"브랜드 {', '.join(sel_brands)}")

t = utils.totals(df)
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("매출", f"{t['매출'] / 1e6:,.1f}백만")
c2.metric("이익", f"{t['이익'] / 1e6:,.1f}백만")
c3.metric("수익률", f"{t['수익률']:.1f}%")
c4.metric("판매수량", f"{t['수량']:,.0f}개")
c5.metric("주문건수", f"{t['주문건수']:,}건")
c6.metric("평균판매가", f"{t['평균판매가']:,.0f}원")

won = st.column_config.NumberColumn(format="localized")  # 1,234,567 (천 단위 쉼표)
pct = st.column_config.NumberColumn(format="%.1f%%")

# ---------------- 브랜드별 / 카테고리별 ----------------
left, right = st.columns(2)
with left:
    st.subheader("🏷️ 브랜드별 요약")
    b = utils.by_brand(df)[["브랜드", "매출", "이익", "수익률", "주문건수", "수량", "평균판매가", "매출비중"]]
    st.dataframe(b, hide_index=True, use_container_width=True,
                 column_config={"매출": won, "이익": won, "평균판매가": won, "주문건수": won, "수량": won, "수익률": pct, "매출비중": pct})
with right:
    st.subheader("📦 카테고리별 요약")
    c = utils.by_category(df)[["브랜드", "카테고리", "매출", "이익", "수익률", "주문건수", "수량"]]
    st.dataframe(c, hide_index=True, use_container_width=True,
                 column_config={"매출": won, "이익": won, "주문건수": won, "수량": won, "수익률": pct})


# ---------------- 베스트셀러 (기간별) ----------------
def render_ranking(sub: pd.DataFrame, sort_key: str, top_n: int, caption: str):
    st.caption(caption)
    if sub.empty:
        st.warning("이 기간에 해당하는 판매 데이터가 없습니다. 해당 기간의 판매 엑셀을 업로드했는지 확인해 주세요.")
        return
    p = utils.by_product(sub)
    p = p[p["수량"] >= min_qty].sort_values(sort_key, ascending=False).head(top_n).reset_index(drop=True)
    if p.empty:
        st.info("조건(최소 판매수량)에 맞는 상품이 없습니다.")
        return
    p["_src"] = p["모델명"].map(lambda m: resolve_image(m, image_map))

    # 상위 5개 카드
    cols = st.columns(5)
    for i, (col, (_, r)) in enumerate(zip(cols, p.head(5).iterrows()), start=1):
        with col:
            src = r["_src"]
            if isinstance(src, str) and src:
                st.image(src, use_container_width=True)
            else:
                st.markdown("🖼️ *이미지 없음*")
            st.markdown(f"**{i}위 · {r['모델명']}**")
            st.caption(f"{r['브랜드']} · {r['카테고리']}")
            st.write(f"매출 {r['매출']:,.0f}원")
            st.write(f"주문 {r['주문건수']:,.0f}건 · 수량 {r['수량']:,.0f}개")
            st.write(f"수익률 {r['수익률']:.1f}%")

    # 전체 TOP N 표 (썸네일 포함)
    table = p.assign(이미지=p["_src"].map(to_table_src))
    cols_show = ["이미지", "브랜드", "모델명"] + (["상품명"] if "상품명" in table.columns else []) + \
                ["카테고리", "주문건수", "수량", "매출", "이익", "수익률", "평균판매가", "평균수수료율"]
    st.dataframe(
        table[cols_show], hide_index=True, use_container_width=True, row_height=64,
        column_config={
            "이미지": st.column_config.ImageColumn("이미지", width="small"),
            "주문건수": won, "수량": won, "매출": won, "이익": won, "평균판매가": won,
            "수익률": st.column_config.ProgressColumn("수익률", format="%.1f%%", min_value=0, max_value=40),
            "평균수수료율": pct,
        },
    )


st.subheader("🏆 판매 우위 상품")
o1, o2 = st.columns(2)
sort_key = o1.radio("정렬 기준", ["매출", "수량", "이익", "수익률"], horizontal=True)
top_n = o2.slider("표시 개수", 5, 50, 10, step=5)

tab1, tab3, tably, taball = st.tabs(["최근 1개월", "최근 3개월", "작년 동기간", "전체 업로드 기간"])

with tab1:
    sub, s, e = utils.window(df, ref_date, months=1)
    render_ranking(sub, sort_key, top_n, f"{s:%Y-%m-%d} ~ {e:%Y-%m-%d} (기준일 포함 최근 1개월)")

with tab3:
    sub, s, e = utils.window(df, ref_date, months=3)
    render_ranking(sub, sort_key, top_n, f"{s:%Y-%m-%d} ~ {e:%Y-%m-%d} (기준일 포함 최근 3개월)")

with tably:
    y1, y2 = st.columns(2)
    span = y1.radio("작년 비교 구간", ["1개월", "2개월", "3개월"], index=2, horizontal=True)
    direction = y2.radio("방향", ["작년 기준일부터 앞으로 (다가오는 시즌)", "작년 기준일까지 (같은 시점의 최근)"], horizontal=False)
    fwd = direction.startswith("작년 기준일부터")
    sub, s, e = utils.window(df, ref_date, months=int(span[0]), years_back=1, forward=fwd)
    have = f"데이터 존재: {sub['출고일'].min():%Y-%m-%d} ~ {sub['출고일'].max():%Y-%m-%d}" if not sub.empty else "데이터 없음"
    render_ranking(sub, sort_key, top_n, f"{s:%Y-%m-%d} ~ {e:%Y-%m-%d} (작년 {span}) · {have}")

with taball:
    render_ranking(df, sort_key, top_n, "업로드한 전체 데이터")

# ---------------- 매출 vs 수익률 ----------------
st.subheader("🎯 매출 vs 수익률 (점 크기 = 판매수량, 전체 업로드 기간)")
st.caption("오른쪽 아래: 많이 팔리지만 마진 낮은 상품 / 왼쪽 위: 마진은 좋은데 덜 팔리는 상품")
p_all = utils.by_product(df)
p_all = p_all[p_all["수량"] >= min_qty]
st.scatter_chart(p_all, x="매출", y="수익률", color="브랜드", size="수량", height=380)

# ---------------- 수수료율별 ----------------
st.subheader("💸 수수료율별 수익률")
f = utils.by_fee(df)
fl, fr = st.columns(2)
with fl:
    st.dataframe(
        f[["수수료율", "주문건수", "수량", "매출", "이익", "수익률"]],
        hide_index=True, use_container_width=True,
        column_config={"수수료율": st.column_config.NumberColumn(format="%.1f%%"),
                       "주문건수": won, "수량": won, "매출": won, "이익": won, "수익률": pct},
    )
with fr:
    chart = f.assign(수수료율=f["수수료율"].map(lambda x: f"{x:g}%")).set_index("수수료율")["수익률"]
    st.bar_chart(chart)

# ---------------- 원본 ----------------
with st.expander("📄 집계에 사용된 원본 데이터"):
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.download_button("CSV 다운로드", df.to_csv(index=False).encode("utf-8-sig"),
                       file_name="zigzag_sales_filtered.csv", mime="text/csv")
