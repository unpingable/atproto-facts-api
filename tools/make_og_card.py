"""Generate the Open Graph preview card.

Run once when the card's text changes; the PNG is committed so the service has
no runtime image dependency.

    python3 tools/make_og_card.py
"""
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
BG = (14, 17, 22)
FG = (238, 241, 245)
MUTED = (150, 160, 172)
ACCENT = (114, 191, 168)
RULE = (38, 44, 52)

F = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"


def font(size, bold=False):
    return ImageFont.truetype(F % ("-Bold" if bold else ""), size)


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

d.rectangle([0, 0, W, 8], fill=ACCENT)

d.text((72, 92), "api.neutral.zone/atproto", font=font(30), fill=ACCENT)
d.text((72, 156), "ATProto Observability", font=font(76, bold=True), fill=FG)
d.text((72, 244), "Facts API", font=font(76, bold=True), fill=FG)

d.text((72, 360),
       "Public, anonymous, read-only aggregate facts",
       font=font(34), fill=FG)
d.text((72, 408),
       "from independent ATProto observation instruments.",
       font=font(34), fill=FG)

d.line([(72, 486), (W - 72, 486)], fill=RULE, width=2)

d.text((72, 516), "Coverage, freshness and observation adequacy travel with", font=font(26), fill=MUTED)
d.text((72, 552), "every fact.  No subject identities are published.", font=font(26), fill=MUTED)

img.save("src/factsapi/assets/og-card.png", "PNG", optimize=True)
print("wrote src/factsapi/assets/og-card.png")
