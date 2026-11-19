"""Translation notes for the seed labels, keyed by their exact English excerpt.

Each row preserves a physical description or a complete interpretive claim.
The English evidence record remains the only citable source.
"""

_LANGUAGES = ("en", "es", "zh-CN", "ko")
_READINGS = {
    "Oil on canvas": [
        ("Medium and support", "Oil on canvas", "Óleo sobre lienzo", "布面油画", "캔버스에 유채"),
    ],
    ("With its bold colors, thick and broken brushwork, and sharply receding lines, "
     "the picture might suggest a nervous energy."): [
        ("Color", "bold colors", "colores intensos", "大胆鲜明的色彩", "대담한 색채"),
        ("Brushwork", "thick and broken brushwork", "pinceladas gruesas y discontinuas", "厚重而断续的笔触",
         "두껍고 끊어지는 붓질"),
        ("Depth", "sharply receding lines", "líneas que se adentran marcadamente en la distancia",
         "明显向纵深退去的线条", "원경으로 가파르게 물러나는 선"),
        ("Possible impression of the whole picture", "The picture might suggest a nervous energy.", "El cuadro podría sugerir una energía nerviosa.",
         "整幅画可能让人感到一种紧张的活力。", "그림 전체가 긴장된 활력을 암시할 수도 있습니다."),
    ],
    ("In the background, Morisot’s soft, feathery brushstrokes suggest a "
     "floral-patterned bedspread and wallpaper."): [
        ("Background brushwork", "soft, feathery brushstrokes in the background", "pinceladas suaves y ligeras como plumas en el fondo",
         "背景中柔和、羽毛般轻盈的笔触", "배경의 부드럽고 깃털처럼 가벼운 붓질"),
        ("Objects suggested by the marks", "The brushstrokes suggest a floral-patterned bedspread and wallpaper.",
         "Las pinceladas sugieren una colcha y un papel pintado con motivos florales.",
         "笔触让人联想到带花卉纹样的床罩和墙纸。", "붓질은 꽃무늬 침대 덮개와 벽지를 연상시킵니다."),
    ],
}


def translation_notes(evidence, language):
    """Return only static wording tied to an accepted, byte-for-byte matching excerpt."""
    column = _LANGUAGES.index(language) + 1
    selected = []
    for record in evidence:
        for row in _READINGS.get(record["excerpt"], ()):
            wording = row[1] if language == "en" else f"{row[1]} / {row[column]}"
            selected.append(f"{row[0]}: {wording}")
    return selected
