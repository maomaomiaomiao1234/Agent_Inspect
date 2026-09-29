"""Author the fixed source PDF independently of reference/candidate JSON.

Requires the optional pdf-fixtures extra. Does not generate reference answers.
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/pdf/document-fixture.pdf"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
pdf = canvas.Canvas(str(OUTPUT), pagesize=A4, invariant=1, pageCompression=1)
pdf.setTitle("Garden water use - document conversion fixture")
pdf.setAuthor("Agent Trace Review - synthetic source document")
width, height = A4
ink, muted, accent = colors.HexColor("#183343"), colors.HexColor("#546775"), colors.HexColor("#176C72")


def line(text, y, size=11, bold=False):
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    pdf.drawString(48, y, text)


def frame(page):
    pdf.setFillColor(accent)
    pdf.rect(48, height - 55, 34, 4, fill=1, stroke=0)
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(48, 35, "Synthetic study / Document conversion fixture v1")
    pdf.drawRightString(width - 48, 35, f"{page} / 2")


frame(1)
line("Field study: garden water use", height - 96, 22, True)
line("A small garden compared two irrigation schedules", height - 136)
line("over three days.", height - 153)
line("1. Recorded water use", height - 207, 15, True)
rows = [
    ["Day", "Drip (L)", "Sprinkler (L)"],
    ["Monday", "12", "18"],
    ["Tuesday", "10", "17"],
    ["Wednesday", "11", "19"],
    ["Total", "33", "54"],
]
left, top, row_height = 48, height - 237, 35
columns = [0, 205, 320, width - 96]
pdf.setFillColor(colors.HexColor("#E9F2F1"))
pdf.rect(left, top - row_height, width - 96, row_height, fill=1, stroke=0)
for i, row in enumerate(rows):
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold" if i in {0, 4} else "Helvetica", 11)
    for j, text in enumerate(row):
        pdf.drawString(left + columns[j] + 12, top - i * row_height - 22, text)
pdf.setStrokeColor(colors.HexColor("#BDCFD3"))
pdf.setLineWidth(0.6)
for i in range(len(rows) + 1):
    pdf.line(left, top - i * row_height, width - 48, top - i * row_height)
for x in columns:
    pdf.line(left + x, top, left + x, top - len(rows) * row_height)
line("The drip schedule used 33 L in total.", height - 458)
line("The sprinkler schedule used 54 L.", height - 475)
pdf.showPage()
frame(2)
line("2. Observations", height - 96, 18, True)
line("Soil moisture was checked each morning before watering.", height - 140)
line("The comparison describes this garden only;", height - 183)
line("it is not a general recommendation.", height - 200)
line("3. Follow-up", height - 273, 18, True)
line("Repeat over a full week and record rainfall.", height - 317)
pdf.save()
fixture = ROOT / "src/agent_trace_review/fixtures/document_conversion/source.pdf"
fixture.parent.mkdir(parents=True, exist_ok=True)
fixture.write_bytes(OUTPUT.read_bytes())
print(OUTPUT)
