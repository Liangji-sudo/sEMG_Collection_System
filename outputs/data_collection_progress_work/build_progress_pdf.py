from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


WORK_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = WORK_DIR.parent / "data_collection_progress"
PDF_PATH = OUTPUT_DIR / "数据采集人员任务进度表_v10_每页20人_编号任务_A4打印版.pdf"


def register_font():
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simsun.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for font_path in candidates:
        if font_path.exists():
            pdfmetrics.registerFont(TTFont("CNFont", str(font_path)))
            return "CNFont"
    return "Helvetica"


def group_no(subject):
    return (subject - 1) // 100 + 1


def subject_tasks(subject):
    g = group_no(subject)
    sid = f"{subject:03d}"
    return [
        (f"L{sid}", "C1"),
        (f"L{sid}", f"C2-{g}"),
        (f"L{sid}", f"C3-{g}"),
        (f"L{sid}", "C4"),
        (f"D{sid}", "连续手势1"),
        (f"D{sid}", "连续手势2"),
    ]


def draw_centered(c, text, x, y, w, h, font, size, bold=False):
    c.setFillColor(colors.black)
    c.setFont(font, size)
    c.drawCentredString(x + w / 2, y + (h - size) / 2 + 1.5, text)


def draw_text(c, text, x, y, w, h, font, size):
    c.setFillColor(colors.black)
    c.setFont(font, size)
    c.drawCentredString(x + w / 2, y + (h - size) / 2 + 1.2, text)


def draw_column(c, subjects, x, top_y, col_widths, row_h, font):
    headers = ["编号", "一级", "二级任务", "完成", "工作人员", "日期"]
    header_h = 14
    table_w = sum(col_widths)
    header_y = top_y - header_h

    c.setFillColor(colors.HexColor("#D9EAF7"))
    c.rect(x, header_y, table_w, header_h, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#B7CBDD"))
    c.setLineWidth(0.4)

    cur_x = x
    for label, width in zip(headers, col_widths):
        c.rect(cur_x, header_y, width, header_h, fill=0, stroke=1)
        draw_centered(c, label, cur_x, header_y, width, header_h, font, 7)
        cur_x += width

    y = header_y
    for idx, subject in enumerate(subjects):
        block_h = row_h * 6
        block_y = y - block_h
        if idx % 2 == 0:
            c.setFillColor(colors.HexColor("#F1FAFE"))
            c.rect(x, block_y, table_w, block_h, fill=1, stroke=0)

        c.setStrokeColor(colors.HexColor("#D1D5DB"))
        c.setLineWidth(0.35)

        x0 = x
        subject_w, task_w, subtask_w, done_w, worker_w, date_w = col_widths

        c.rect(x0, block_y, subject_w, block_h, fill=0, stroke=1)
        draw_centered(c, f"{subject:03d}", x0, block_y, subject_w, block_h, font, 7)
        x0 += subject_w

        c.rect(x0, block_y + row_h * 2, task_w, row_h * 4, fill=0, stroke=1)
        draw_centered(c, f"L{subject:03d}", x0, block_y + row_h * 2, task_w, row_h * 4, font, 7)
        c.rect(x0, block_y, task_w, row_h * 2, fill=0, stroke=1)
        draw_centered(c, f"D{subject:03d}", x0, block_y, task_w, row_h * 2, font, 7)
        x0 += task_w

        task_rows = subject_tasks(subject)
        for r, (_, subtask) in enumerate(task_rows):
            row_y = y - row_h * (r + 1)
            c.rect(x0, row_y, subtask_w, row_h, fill=0, stroke=1)
            draw_text(c, subtask, x0, row_y, subtask_w, row_h, font, 7)
            c.rect(x0 + subtask_w, row_y, done_w, row_h, fill=0, stroke=1)
            c.rect(x0 + subtask_w + done_w, row_y, worker_w, row_h, fill=0, stroke=1)
            c.rect(x0 + subtask_w + done_w + worker_w, row_y, date_w, row_h, fill=0, stroke=1)

        y = block_y


def build_pdf():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    font = register_font()
    c = canvas.Canvas(str(PDF_PATH), pagesize=A4)
    width, height = A4

    margin_x = 18
    margin_top = 20
    title_h = 18
    gap = 12
    col_widths = [30, 30, 62, 42, 54, 54]
    row_h = 12.25
    table_w = sum(col_widths)

    for page in range(20):
        first = page * 20 + 1
        left_subjects = list(range(first, first + 10))
        right_subjects = list(range(first + 10, first + 20))
        title = f"数据采集人员任务进度表  第 {page + 1} 页：{first:03d}-{first + 19:03d}"

        c.setFillColor(colors.HexColor("#1F4E79"))
        c.rect(margin_x, height - margin_top - title_h, width - margin_x * 2, title_h, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(font, 11)
        c.drawCentredString(width / 2, height - margin_top - 13, title)

        top_y = height - margin_top - title_h - 4
        left_x = margin_x
        right_x = margin_x + table_w + gap
        draw_column(c, left_subjects, left_x, top_y, col_widths, row_h, font)
        draw_column(c, right_subjects, right_x, top_y, col_widths, row_h, font)

        c.setFillColor(colors.HexColor("#666666"))
        c.setFont(font, 7)
        c.drawRightString(width - margin_x, 12, f"{page + 1}/20")
        c.showPage()

    c.save()
    print(PDF_PATH)


if __name__ == "__main__":
    build_pdf()
