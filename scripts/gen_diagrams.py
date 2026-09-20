"""Sinh so do kien truc: moi so do ra 2 file tu CUNG mot layout.

  docs/diagrams/<ten>.svg     -> GitHub render thang trong README
  docs/diagrams/<ten>.drawio  -> mo bang draw.io / extension VS Code de sua

Chay lai sau khi sua layout o duoi:  python scripts/gen_diagrams.py
"""
import os
import xml.sax.saxutils as sx

OUT = "docs/diagrams"

FONT = ("ui-sans-serif,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,"
        "'Noto Sans',sans-serif")

# Bang mau: nen sang co dinh de doc duoc ca tren GitHub theme toi.
INK = "#1f2430"
MUTED = "#5b6472"
LINE = "#8c98a8"
BG = "#ffffff"

PAL = {
    "src":    ("#f4f5f7", "#b9bfc9"),
    "task":   ("#e7f0fd", "#7ba3e0"),
    "graph":  ("#e6f4ec", "#6fb492"),
    "llm":    ("#fdf0e4", "#dfa267"),
    "store":  ("#ece9fa", "#9b90d8"),
    "out":    ("#fdeaf1", "#dd8fae"),
    "obs":    ("#fdf0e4", "#dfa267"),
    "html":   ("#e7f0fd", "#7ba3e0"),
    "end":    ("#f4f5f7", "#98a2b3"),
    "group":  ("#fbfcfe", "#c6cedb"),
}


class Node:
    def __init__(self, nid, x, y, w, h, title, lines=(), kind="task",
                 group=False, bold_title=True):
        self.id, self.x, self.y, self.w, self.h = nid, x, y, w, h
        self.title, self.lines, self.kind = title, list(lines), kind
        self.group, self.bold_title = group, bold_title

    def anchor(self, side):
        cx, cy = self.x + self.w / 2.0, self.y + self.h / 2.0
        return {"n": (cx, self.y), "s": (cx, self.y + self.h),
                "w": (self.x, cy), "e": (self.x + self.w, cy)}[side]


class Edge:
    def __init__(self, src, ssid, dst, dsid, label="", dashed=False, pts=None,
                 lpos=None):
        self.src, self.ssid, self.dst, self.dsid = src, ssid, dst, dsid
        self.label, self.dashed, self.pts = label, dashed, list(pts or [])
        # lpos: dat nhan tay. Can khi nhieu canh cung do ve mot dich - nhan tinh
        # tu doan cuoi se chong len nhau.
        self.lpos = lpos


# --------------------------------------------------------------------- SVG

def esc(t):
    return sx.escape(str(t))


def svg_node(n):
    fill, stroke = PAL[n.kind]
    o = []
    rx = 10 if not n.group else 12
    dash = ' stroke-dasharray="6 4"' if n.group else ""
    o.append(f'<rect x="{n.x:g}" y="{n.y:g}" width="{n.w:g}" height="{n.h:g}" rx="{rx:g}" '
             f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"{dash}/>')

    if n.group:
        o.append(f'<text x="{n.x + 14:g}" y="{n.y + 21:g}" font-family="{FONT}" font-size="12.5" '
                 f'font-weight="600" fill="{MUTED}">{esc(n.title)}</text>')
        for i, ln in enumerate(n.lines):
            o.append(f'<text x="{n.x + 14:g}" y="{n.y + 38 + i * 14:g}" font-family="{FONT}" font-size="11" '
                     f'fill="{MUTED}">{esc(ln)}</text>')
        return "".join(o)

    cx = n.x + n.w / 2.0
    nsub = len(n.lines)
    total = 15 + nsub * 13
    top = n.y + (n.h - total) / 2.0 + 12
    weight = "600" if n.bold_title else "400"
    o.append(f'<text x="{cx:g}" y="{top:g}" text-anchor="middle" font-family="{FONT}" '
             f'font-size="13" font-weight="{weight}" fill="{INK}">{esc(n.title)}</text>')
    for i, ln in enumerate(n.lines):
        o.append(f'<text x="{cx:g}" y="{top + 15 + i * 13:g}" text-anchor="middle" font-family="{FONT}" '
                 f'font-size="10.5" fill="{MUTED}">{esc(ln)}</text>')
    return "".join(o)


def svg_edge(e, nodes):
    a = nodes[e.src].anchor(e.ssid)
    b = nodes[e.dst].anchor(e.dsid)
    pts = [a, *e.pts, b]
    d = " ".join(f"{p[0]:g},{p[1]:g}" for p in pts)
    dash = ' stroke-dasharray="5 4"' if e.dashed else ""
    o = [f'<polyline points="{d}" fill="none" stroke="{LINE}" stroke-width="1.5" '
         f'marker-end="url(#ar)"{dash}/>']
    if e.label:
        if e.lpos:
            mx, my = e.lpos
            my += 18          # lpos la diem dat chu, bu lai offset ben duoi
        else:
            # Mac dinh: giua doan ap chot cuoi.
            p, q = pts[-2], pts[-1]
            mx, my = (p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0
        w = 6.4 * len(e.label) + 10
        o.append(f'<rect x="{mx - w / 2:g}" y="{my - 18:g}" width="{w:g}" height="16" rx="4" '
                 f'fill="{BG}" opacity="0.95"/>')
        o.append(f'<text x="{mx:g}" y="{my - 6:g}" text-anchor="middle" font-family="{FONT}" '
                 f'font-size="10.5" fill="{MUTED}">{esc(e.label)}</text>')
    return "".join(o)


def write_svg(name, w, h, nodes, edges, caption=""):
    order = sorted(nodes.values(), key=lambda n: 0 if n.group else 1)
    body = []
    body.append(f'<rect width="{w:g}" height="{h:g}" fill="{BG}"/>')
    for n in order:
        body.append(svg_node(n))
    for e in edges:
        body.append(svg_edge(e, nodes))
    if caption:
        body.append(f'<text x="{18:g}" y="{h - 14:g}" font-family="{FONT}" font-size="11" '
                    f'fill="{MUTED}">{esc(caption)}</text>')

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{:g}" height="{:g}" '
        'viewBox="0 0 {:g} {:g}" role="img">'
        '<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="{}"/></marker></defs>{}</svg>'.format(w, h, w, h, LINE, "".join(body))
    )
    path = os.path.join(OUT, name + ".svg")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(svg + "\n")
    return path


# ------------------------------------------------------------------ drawio

def drawio_style(n):
    fill, stroke = PAL[n.kind]
    if n.group:
        return (f"rounded=1;arcSize=8;fillColor={fill};strokeColor={stroke};dashed=1;"
                "verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;"
                f"fontSize=12;fontColor={MUTED};html=1;")
    return (f"rounded=1;arcSize=14;fillColor={fill};strokeColor={stroke};html=1;"
            f"fontSize=12;fontColor={INK};whiteSpace=wrap;")


def drawio_value(n):
    head = f"<b>{esc(n.title)}</b>" if n.bold_title else esc(n.title)
    if n.lines:
        sub = "<br/>".join(f'<font style="font-size:10px;color:{MUTED}">{esc(ln)}</font>' for ln in n.lines)
        return sx.escape(head + "<br/>" + sub)
    return sx.escape(head)


def write_drawio(name, w, h, nodes, edges, title):
    cells = []
    for n in nodes.values():
        cells.append(
            f'<mxCell id="{n.id}" value="{drawio_value(n)}" style="{drawio_style(n)}" vertex="1" parent="1">'
            f'<mxGeometry x="{n.x:g}" y="{n.y:g}" width="{n.w:g}" height="{n.h:g}" as="geometry"/>'
            '</mxCell>')
    for i, e in enumerate(edges):
        dash = "dashed=1;" if e.dashed else ""
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;"
                 f"strokeColor={LINE};fontSize=10;fontColor={MUTED};"
                 f"endArrow=block;endFill=1;{dash}")
        cells.append(
            f'<mxCell id="e{i}" value="{sx.escape(esc(e.label))}" style="{style}" edge="1" parent="1" '
            f'source="{e.src}" target="{e.dst}"><mxGeometry relative="1" as="geometry"/>'
            '</mxCell>')

    dtitle, body = esc(title), "".join(cells)
    xml = (
        '<mxfile host="app.diagrams.net" type="device">'
        f'<diagram name="{dtitle}" id="{name}">'
        '<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" '
        'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{w:g}" pageHeight="{h:g}" math="0" shadow="0">'
        f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root>'
        '</mxGraphModel></diagram></mxfile>'
    )
    path = os.path.join(OUT, name + ".drawio")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(xml + "\n")
    return path


def emit(name, w, h, nodes, edges, title, caption=""):
    nd = {n.id: n for n in nodes}
    a = write_svg(name, w, h, nd, edges, caption)
    b = write_drawio(name, w, h, nd, edges, title)
    print(f"  {a:<58} {b}")


# =====================================================================
# 1. Toan canh
# =====================================================================

def diagram_architecture():
    """Luong end-to-end. Canh phai phai khop voi airflow/dags/daily_news_dag.py:
    build_weather KHONG phu thuoc check_sources, va finalize KHONG nam sau purge
    - ca hai deu treo thang tu deliver.
    """
    N = [
        Node("box_af", 275, 45, 680, 292, "Airflow  ·  DAG daily_news_digest",
             ["lịch 08:00 giờ VN, T2–T6  ·  mỗi task chạm app chạy bằng "
              "@task.external_python"], kind="group", group=True),

        Node("src", 45, 190, 200, 110, "42 nguồn tin",
             ["40 RSS  ·  1 HF Papers", "1 GitHub Trending",
              "31 publisher, 15 chuyên mục"], kind="src"),

        Node("preflight", 295, 100, 125, 48, "preflight", ["đọc sources.yaml"]),
        Node("create_run", 295, 165, 125, 48, "create_run", ["mở pipeline_run"]),
        Node("ingest", 450, 100, 150, 66, "ingest  ×42",
             ["dynamic task mapping", "fetch → parse → lưu"]),
        Node("check", 450, 185, 150, 48, "check_sources", ["feed chết / đóng băng"]),
        Node("digest", 630, 95, 150, 62, "build_digest",
             ["LangGraph, 7 node", "→ sơ đồ bên dưới"], kind="graph"),
        Node("deliver", 810, 185, 125, 56, "deliver",
             ["ALL_DONE", "retry ×3"]),
        Node("weather", 450, 278, 150, 44, "build_weather", ["card thời tiết"]),
        Node("purge", 630, 278, 150, 44, "purge_old_data", ["giữ 14 ngày"]),
        Node("finalize", 810, 278, 125, 44, "finalize", ["ALL_DONE"]),

        Node("llm", 1010, 73, 180, 105, "LLM  ·  chuỗi ưu tiên",
             ["gemini → vertex → openai", "provider hỏng bị bỏ hẳn",
              "cho cả lần chạy"], kind="llm"),
        Node("chat", 1010, 175, 180, 75, "Google Chat",
             ["webhook, cardsV2", "mỗi nhóm một message"], kind="out"),

        Node("pg", 275, 375, 680, 105, "Postgres  ·  schema news",
             ["article · article_summary · digest      |      pipeline_run · "
              "node_span · llm_call · app_log · source_health",
              "Mọi task đều ghi trace vào đây. Trang trạng thái và Grafana "
              "đọc lại từ đây — xem sơ đồ Quan sát."], kind="store"),
    ]
    E = [
        Edge("src", "e", "preflight", "w", "", pts=[(270, 245), (270, 124)]),
        Edge("preflight", "e", "ingest", "w", "", pts=[(435, 124), (435, 133)]),
        Edge("create_run", "e", "ingest", "w", "", pts=[(435, 189), (435, 133)]),
        Edge("create_run", "s", "weather", "w", "", pts=[(357, 300)]),
        Edge("ingest", "s", "check", "n"),
        Edge("check", "e", "digest", "w", "", pts=[(615, 209), (615, 126)]),
        Edge("digest", "n", "llm", "w", "gọi LLM", dashed=True,
             pts=[(705, 72), (995, 72), (995, 125)]),
        Edge("digest", "e", "deliver", "w", "", pts=[(795, 126), (795, 213)]),
        Edge("weather", "e", "deliver", "w", "", pts=[(795, 300), (795, 213)]),
        Edge("deliver", "s", "finalize", "n"),
        Edge("deliver", "s", "purge", "n", "", pts=[(872, 260), (705, 260)]),
        Edge("deliver", "e", "chat", "w", ""),
        Edge("box_af", "s", "pg", "n", "ghi trace + dữ liệu", dashed=True,
             pts=[(615, 356)]),
    ]
    emit("architecture", 1220, 515, N, E,
         "Kiến trúc tổng thể",
         "Nét đứt = ghi xuống / gọi ra ngoài luồng điều phối.")


# =====================================================================
# 2. LangGraph
# =====================================================================

def diagram_langgraph():
    y, w, h = 70, 125, 62
    xs = [30, 180, 330, 480, 630, 780, 930]
    spec = [
        ("load", "load", ["nạp bài trong", "cửa sổ thời gian"]),
        ("cluster", "cluster", ["gom trùng", "simhash + URL"]),
        ("rank", "rank", ["heuristic", "KHÔNG gọi LLM"]),
        ("enrich", "enrich", ["fetch fulltext", "riêng shortlist"]),
        ("summarize", "summarize", ["1 LLM / bài", "chạy song song"]),
        ("compose", "compose", ["biên tập", "1 LLM / nhóm"]),
        ("render", "render", ["payload cardsV2", "lưu trạng thái pending"]),
    ]
    N = [Node(i, xs[k], y, w, h, t, ls, kind="graph")
         for k, (i, t, ls) in enumerate(spec)]
    N.append(Node("end", 1090, 79, 70, 44, "END", [], kind="end"))

    E = []
    for k in range(len(spec) - 1):
        E.append(Edge(spec[k][0], "e", spec[k + 1][0], "w"))
    E.append(Edge("render", "e", "end", "w"))
    E.append(Edge("load", "s", "end", "s", "không có bài mới → dừng",
                  pts=[(92, 225), (1125, 225)], lpos=(300, 225)))
    E.append(Edge("summarize", "s", "end", "s", "không tóm tắt được → dừng",
                  pts=[(692, 180), (1125, 180)], lpos=(860, 180)))

    emit("langgraph", 1190, 292, N, E,
         "LangGraph build_digest",
         "Hai nhánh điều kiện: không có bài mới, hoặc không tóm tắt được → dừng sớm.")


# =====================================================================
# 3. Quan sat
# =====================================================================

def diagram_observability():
    N = [
        Node("box_biz", 30, 28, 320, 400, "NGHIỆP VỤ",
             ["bản tin hôm nay ra sao?"], kind="group", group=True),
        Node("box_res", 400, 28, 470, 400, "TÀI NGUYÊN",
             ["máy có chịu nổi không?"], kind="group", group=True),

        Node("status", 50, 80, 280, 120, "Trang trạng thái  :18081",
             ["run  ·  chi phí LLM  ·  42 nguồn",
              "nội dung bản tin  ·  dung lượng bảng",
              "http.server, đọc thẳng SQL"], kind="html"),
        Node("pg", 50, 300, 280, 105, "Postgres  ·  schema news",
             ["v_run_overview  ·  llm_call",
              "source_health  ·  digest"], kind="store"),

        Node("grafana", 560, 80, 280, 120, "Grafana  :18091",
             ["dashboard news-host",
              "CPU  ·  RAM  ·  đĩa  ·  mạng  ·  I/O",
              "runtime của tiến trình Postgres"], kind="obs"),
        Node("prom", 560, 230, 280, 70, "Prometheus  :18090",
             ["pull mỗi 15s  ·  giữ 15 ngày"], kind="obs"),
        Node("nodeexp", 420, 345, 190, 70, "node_exporter  :18092",
             ["CPU · RAM · đĩa · mạng"], kind="obs"),
        Node("pgexp", 650, 345, 190, 70, "postgres_exporter  :18093",
             ["kết nối  ·  cache hit"], kind="obs"),
    ]
    E = [
        Edge("pg", "n", "status", "s", "đọc thẳng SQL"),
        Edge("nodeexp", "n", "prom", "s", "", pts=[(515, 322), (640, 322)]),
        Edge("pgexp", "n", "prom", "s", "", pts=[(745, 322), (760, 322)]),
        Edge("prom", "n", "grafana", "s"),
        Edge("status", "e", "grafana", "w", "link ở phụ đề", dashed=True),
    ]
    emit("observability", 900, 470, N, E,
         "Hai mặt quan sát",
         "Không panel nào lặp lại giữa hai bên. Pipeline CỐ Ý không được scrape: "
         "chạy 1 lần/ngày vài phút, mô hình pull sẽ trượt gần hết lượt.")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    print("sinh so do:")
    diagram_architecture()
    diagram_langgraph()
    diagram_observability()
