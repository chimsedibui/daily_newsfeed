"""Dinh nghia cac nhom ban tin.

Moi nhom = mot message rieng tren Google Chat. Thu tu gui theo `order`.

`weather` khong lay tu feed va khong goi LLM - no duoc dung tu API thoi tiet o
weather.py. De o day de phan `deliver` doi xu voi ca ba nhom nhu nhau.
"""
from __future__ import annotations

from typing import NamedTuple


class Group(NamedTuple):
    key: str
    title: str          # tieu de card
    icon: str
    order: int          # thu tu gui
    default_size: int   # so tin toi da trong ban tin
    editor_focus: str   # nhet vao prompt bien tap


SERIOUS = Group(
    key="serious",
    title="Tin nghiêm túc",
    icon="📊",
    order=1,
    default_size=12,
    editor_focus=(
        "Doc gia la ky su AI/du lieu, dong thoi theo doi thi truong tai chinh. "
        "Uu tien theo thu tu: (1) chinh sach tien te va cong bo cua FED hoac ngan "
        "hang trung uong; (2) paper va kien truc mo hinh AI moi; (3) dong thai cua "
        "cac hang cong nghe va quy dau tu lon; (4) chung khoan Viet Nam va the gioi; "
        "(5) chinh tri the gioi co anh huong kinh te. "
        "Bo qua tin cau view, tin doi tu nguoi noi tieng, tin ben le."
    ),
)

LIFE = Group(
    key="life",
    title="Đời sống",
    icon="🏐",
    order=2,
    default_size=6,
    editor_focus=(
        "Day la phan giai tri cuoi ban tin. Doc gia quan tam: xe hoi va xe may "
        "(mau moi, gia ban, danh gia), du lich, bong da, bong chuyen. "
        "Uu tien tin co thong tin cu the (mau xe moi ra mat, ket qua tran dau, "
        "diem den kem chi phi) hon tin cam thán chung chung. "
        "Neu co tran bong chuyen hoac bong da dang chu y thi dat len dau."
    ),
)

WEATHER = Group(
    key="weather",
    title="Thời tiết",
    icon="🌤️",
    order=3,
    default_size=0,      # khong lay tu feed
    editor_focus="",
)

ALL: dict[str, Group] = {g.key: g for g in (SERIOUS, LIFE, WEATHER)}

NEWS_GROUPS: tuple[Group, ...] = (SERIOUS, LIFE)


def get(key: str) -> Group:
    """Nhom khong biet -> dua ve serious thay vi nem loi: mot dong sai trong
    sources.yaml khong duoc lam mat bai."""
    return ALL.get(key, SERIOUS)


def ordered(keys) -> list[Group]:
    return sorted((get(k) for k in keys), key=lambda g: g.order)
