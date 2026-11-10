"""Parse explicit bilingual requests and their artwork scope."""

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True, slots=True)
class QueryRequest:
    start: int
    end: int
    artwork_id: str
    facet: str


_BEDROOM = "aic-28560"
_TOILETTE = "aic-11723"
_ARTWORK_REFERENCES = {
    _BEDROOM: re.compile(
        r"(?i:\baic-28560\b|[\"“]the bedroom[\"”])"
        r"|(?i:\bvan gogh[’']s) (?:[Tt]he )?Bedroom\b"
        r"|\b(?:[Tt]he Bedroom|THE BEDROOM)\b|《\s*卧室\s*》|[\"“]卧室[\"”]"
    ),
    _TOILETTE: re.compile(r"\baic-11723\b|\bwoman at her toilette\b|梳妆(?:的)?女[子人]", re.I),
}
_CURRENT_REFERENCE = re.compile(
    r"\b(?:(?:this|current|selected) (?:artwork|painting|picture|work|canvas)|here"
    r"|(?:artwork|painting|picture|work) in front of (?:me|us))\b"
    r"|这[幅张件](?:画|作品)|本(?:画|作品)|眼前这[幅张件]|这里", re.I,
)
_DEPENDENT_REFERENCE = re.compile(
    r"\b(?:it|its|they|them|their|that (?:artwork|painting|picture|work|one)|what about|how about)\b"
    r"|它|其中|那[幅张件](?:画|作品)?|那.{0,6}呢", re.I,
)
_COMPARISON = re.compile(
    r"\b(?:compar(?:e|ed|es|ing|ison)|differ(?:ent|ence|s)?|versus|vs|both (?:works|paintings))\b"
    r"|对比|比较|相比|区别|不同|两幅(?:画|作品)", re.I,
)

_BOTH_WORKS = re.compile(r"\bboth (?:works|paintings)\b|两幅(?:画|作品)", re.I)
_SAME_ATTRIBUTE = re.compile(r"\b(?:same|similar|identical)\b|一样|相同|相似|一致", re.I)
_SENTENCE_END = re.compile(r"[!?;。！？；]+|(?<!\d)\.|\.(?!\d)")
_RESTART = re.compile(
    r"(?:,\s*)?\b(?:and|but|also)\s+(?=(?:what|which|how|why|who|when|where"
    r"|did|do|does|is|are|was|were|can|could|would|has|have|will|tell|describe|explain)\b)"
    r"|(?:,\s*)?\b(?:and|but)\s+(?=(?:please\s+)?(?:tell|describe|explain|show|identify|name|give)\b)"
    r"|[,，]\s*(?=另外|此外|还想问|也想问|再问|还有|又(?:是)?(?:怎么|怎样|如何|为什么|用|有))"
    r"|(?:\s*[,，])?\s*(?:以及|并且?)\s*(?=请(?:问|描述|解释|说说|告诉)|能否|是否|什么|哪(?:种|些)|怎么|如何)",
    re.I,
)
_COMMA = re.compile(r"[,，]\s*")
_QUESTION = re.compile(
    r"^\s*(?:what|which|how|why|who|when|where|did|do|does|is|are|was|were|can|could|would"
    r"|has|have|will|on which|on what|in what)\b"
    r"|什么|多少|多宽|多高|多大|怎么|怎样|如何|何种|有何|何处|哪[里儿]|哪(?:一)?[个种类件些样]"
    r"|是否|是不是|有没有|还是|谁|[吗呢]\s*$", re.I,
)
_COMMAND = re.compile(
    r"^\s*(?:please\s+)?(?:tell|describe|explain|show|identify|name|compare|give)\b"
    r"|\bi(?:\s+(?:also )?want|(?: would|['’]d) (?:also )?like) to (?:know|understand)\b"
    r"|\b(?:i (?:wonder|was (?:also )?wondering)|i(?:['’]m| am) curious)"
    r" (?:if|whether|what|which|how|why|who|when|where)\b"
    r"|请问|请(?:描述|介绍|解释|告诉)|告诉我|说说|想(?:知道|了解)|能否", re.I,
)
_BARE_ENGLISH = frozenset(
    """material materials medium oil canvas painting support brushwork brushstroke brushstrokes
    brush stroke strokes marks mark color colors colour colours nervous energy perspective receding
    lines background wallpaper bedspread and or paint surface""".split()
)
_BARE_CHINESE = re.compile(
    r"(?:材料|材质|媒材|媒介|油画|画布|底材|笔触|画痕|笔痕|笔法|落笔|颜色|色彩"
    r"|紧张|透视|背景|墙纸|床罩|和|与|及|\s)+"
)

_SUPPORT = r"(?:paper|canvas|cloth|fabric|linen|wood(?:en panel)?|woven (?:fabric|surface))"
_SUPPORT_CHOICE = (
    rf"\bpainted (?:on|onto) (?:an? |the )?{_SUPPORT}(?:,\s*| )"
    rf"(?:or|rather than) (?:on |onto )?(?:an? |the )?{_SUPPORT}\b"
    r"|(?:画|绘)在(?:纸(?:张|面)?|布(?:面|料)?|织物|亚麻布|木板)上(?:的)?\s*[,，]?\s*(?:还是|或是?)"
    r"(?:(?:画|绘)在)?(?:纸(?:张|面)?|布(?:面|料)?|织物|亚麻布|木板)上"
)
_PAINT_AND_SUPPORT = re.compile(
    r"\b(?:paint and (?:the )?(?:surface|support)|(?:surface|support) and (?:the )?paint)\b"
    r"|\bpaint(?:ed)?(?: (?:it|this|that))? (?:with and (?:on|onto)|on and with)\b"
    r"|颜料(?:和|与|以及|及)底材|(?:用|拿)什么(?:颜料|画).{0,14}(?:画|绘)在.{0,10}(?:什么|哪种)", re.I,
)
_MEDIUM_CHECK = re.compile(
    rf"\boil (?:paint(?:ing)? )?on (?:an? |the )?{_SUPPORT}\b|布面油画|(?:布上|画布上).{{0,10}}油画", re.I,
)
_BEHIND = re.compile(r"\b(?:behind|past) (?:her|(?:the )?(?:figure|woman|sitter))\b|身后|背后", re.I)
_SCENE_SETTING = re.compile(r"\b(?:background|back there)\b|背景|身后", re.I)
_REFERRED_PATTERN = re.compile(
    r"\b(?:the|that|those) (?:pattern|flowers?|wallpaper|bedspread)\b|^\s*(?:那[些种个])?(?:花纹|图案)", re.I,
)
_MARKS = re.compile(r"\b(?:marks?|strokes?)\b|画痕|笔痕|笔迹|笔触", re.I)
_REFERRED_MARKS = re.compile(
    r"\b(?:these|those|the) (?:brush[- ]?strokes?|brushwork|marks?|strokes?)\b"
    r"|(?:这些|那些|这部分|那部分|那一部分)(?:画痕|笔触|痕迹)", re.I,
)
_ASPECT_PRONOUN = r"(?:it|they|them|(?:these|those|that)(?=\s*(?:[,.)]|$|are\b|were\b|like\b)))"
_ASPECT_REFERENCE = re.compile(
    rf"\b{_ASPECT_PRONOUN}\b|它(?:们)?|(?:这些|那些)(?=\s*(?:$|[,，]|是|有|像|往|向|看))|那部分",
    re.I,
)
_DESCRIPTION_OF_REFERENCE = re.compile(
    rf"\b(?:describe|characteri[sz]e)\s+{_ASPECT_PRONOUN}\b"
    rf"|\b{_ASPECT_PRONOUN}\s+(?:look|appear|seem)\b"
    rf"|\bwhat (?:is|are|was|were) {_ASPECT_PRONOUN}\s+like\b"
    r"|(?:描述|形容)(?:一?下)?(?:它(?:们)?|这些|那些)(?:的?样子|呢|吗|$)"
    r"|(?:它(?:们)?|这些|那些)(?:看起来|是?(?:什么样|怎样的)|给人什么感觉)", re.I,
)
_LINE_MOVEMENT = (
    r"\b(?:recede|receding|go back(?!\s+to\b|\s+in time\b)|run back|extend into"
    r"|(?:run|go|extend|angle|lead|draw) (?:toward(?:s)?|into) (?:the )?(?:back|distance|depth)"
    r"|lead (?:the |your )?eye)\b"
    r"|(?:向|往|朝)(?:里|远处|深处|纵深)(?:延伸|收缩|收拢|收|退|伸|斜)"
    r"|(?:视线|目光)(?:引|带)(?:向|往)(?:画面|房间)?(?:纵深|深处|远处|里面)"
)
_RECESSION = re.compile(_LINE_MOVEMENT, re.I)
_MARK_SUGGESTION = re.compile(r"\b(?:suggest|depict|represent|evoke)\b|暗示|联想到", re.I)
_STROKE_DESCRIPTION = re.compile(
    r"\b(?:describe|characteri[sz]e)\b.{0,60}\b(?:brushwork|brush[- ]?strokes?|strokes?|marks?)\b"
    r"|(?:描述|形容).{0,22}(?:笔触|画痕|笔法)|(?:笔触|画痕|笔法).{0,18}(?:什么样|什么特点|看起来怎样)",
    re.I,
)
# Nominal mentions can introduce a referent in a statement without becoming requests.
_ASPECT_NOUNS = {
    "line_recession": re.compile(r"\b(?:lines|perspective)\b|透视|线条|斜线", re.I),
    "brushwork_appearance": re.compile(
        r"\b(?:brushwork|brush[- ]?strokes?|(?:brush|paint) marks?|strokes)\b|笔触|画痕|笔痕|笔法",
        re.I,
    ),
    "color_appearance": re.compile(r"\b(?:colou?rs?|palette)\b|颜色|色彩|用色", re.I),
    "background_objects": re.compile(r"\b(?:wallpaper|bedspread|floral pattern)\b|墙纸|床罩|花纹", re.I),
    "visual_effect": re.compile(r"\b(?:mood|atmosphere|nervous energy)\b|氛围|观感|紧张的", re.I),
}
_PIGMENT_MATERIAL = re.compile(
    r"\b(?:pigments?|colou?rants?|dyes?)\b|色料|色素|颜料(?:的)?成分|颜料名(?:字|称)?"
    r"|(?:哪些|哪几[种类样])(?:具体(?:的)?)?(?:绘画)?颜料"
    r"|(?:确切|具体|特定)(?:的)?(?:[红蓝绿黄黑白紫橙]色)?颜料"
    r"|(?:哪种|什么)(?:红色|蓝色|绿色|黄色|黑色|白色|紫色|橙色)颜料", re.I,
)
# A composition predicate must govern the palette or its pronoun, rather than an
# incidental object such as the frame mentioned in a question about colors.
_COMPOSITION_VERB = r"(?:made (?:of|from)|(?:mixed|derived|produced) (?:from|out of|with))"
_MIX_WITH = r"(?:拿|用)(?:的)?什么.{0,12}(?:调|配)(?:成|出|制)"
_PALETTE_NAME = r"(?:colou?rs?|palette)"
_PALETTE_COMPOSITION = re.compile(
    rf"\b{_PALETTE_NAME}\s+(?:(?:is|are|was|were)\s+)?{_COMPOSITION_VERB}\b"
    rf"|\b(?:what went into|chemical composition of|(?:what|which)\b.{{0,40}}"
    rf"\buse(?:d)? to (?:make|mix|produce|create))\s+(?:(?:the|these|those|its)\s+)?{_PALETTE_NAME}\b"
    r"|(?:颜色|色彩|用色)(?:是)?(?:拿|用|由|怎么|怎样|如何).{0,18}(?:调|配)(?:成|出|制)"
    rf"|{_MIX_WITH}(?:了|来|的)?(?:这些|那些|这种|那种|画中|画里的)?(?:颜色|色彩)"
    r"|(?:颜色|色彩)(?:的)?(?:成分|原料)(?:是什么|有哪些)", re.I,
)
_REFERENT_COMPOSITION = re.compile(
    rf"\b{_ASPECT_PRONOUN}\s+(?:(?:is|are|was|were)\s+)?{_COMPOSITION_VERB}\b"
    rf"|\bwhat went into\s+{_ASPECT_PRONOUN}\b"
    r"|(?:它(?:们)?|这些|那些)(?:是)?(?:拿|用|由|怎么|怎样|如何).{0,18}(?:调|配)(?:成|出|制)",
    re.I,
)
_APPEARANCE = re.compile(
    r"\b(?:looks?|like|appearance|smooth|continuous|thick|broken|rough|soft|feathery|delicate"
    r"|hard[- ]edged|describe|characteri[sz]e|compare|differ)\b"
    r"|看起[来來]|像|形容|描述|柔和|粗重|厚重|断续|轻柔|什么样|有何区别", re.I,
)

_SUPPORTED = {
    "medium": (
        r"\b(?:materials?|medium|made of|oil|canvas)\b|材料|材质|媒材|媒介|油画|画布",
    ),
    "paint_medium": (
        r"\b(?:what|which) (?:(?:kind|type|class|sort|family) of )?(?:paint|painting medium)\b"
        r"|\b(?:what|which)\b.{0,55}\bpaint(?:ed)?(?: (?:it|this|that))? with\b"
        r"|\bpainted with (?:what|which)\b"
        r"|\b(?:painted|done|executed) (?:in|with) (?:oils?|watercolou?rs?|tempera|acrylics?|gouache)\b"
        r"|(?:什么|哪(?:一)?[种类样])(?:(?:类型|种类|类别|一类)的?)?"
        r"(?:绘画)?(?:画料|颜料|油彩|媒介|媒材)"
        r"|用什么(?:画料|颜料|油彩)画"
        r"|用(?:油彩|水彩|丙烯|蛋彩)(?:颜料)?(?:来)?(?:画|绘)",
        r"\b(?:oil(?: paints?)?|watercolou?rs?|tempera|acrylics?|gouache)\b.{0,15}"
        r"\b(?:or|rather than)\b.{0,15}\b(?:oil(?: paints?)?|watercolou?rs?|tempera|acrylics?|gouache)\b"
        r"|(?:油彩|油画|水彩|丙烯|蛋彩)(?:还是|或)(?:油彩|油画|水彩|丙烯|蛋彩)",
    ),
    "painting_support": (
        r"\b(?:painting support|substrate)\b"
        r"|\b(?:what|which)\b.{0,62}\bpaint(?:ed)?(?: (?:it|this|that))? (?:on|onto)\s*$"
        r"|\b(?:what|which) (?:(?:kind|type) of )?(?:surface|support)\b.{0,44}"
        r"\b(?:paint(?:ed|ing)?|picture|artwork|canvas)\b"
        r"|\bpainted (?:on|onto) (?:what|which)\b"
        r"|\b(?:what|which) (?:(?:kind|type) of )?surface\b.{0,40}"
        r"\buse[ds]?(?: for (?:the|this|that|a|his|her)\b|\s*$)"
        r"|(?:画|绘)在.{0,16}(?:什么|哪[种类样]).{0,12}上|底材|承载面",
        _SUPPORT_CHOICE,
    ),
    "brushwork_appearance": (
        r"\b(?:brushwork|brush[- ]?strokes?|(?:brush|paint) marks?|(?:painter|artist)[’']s marks?)\b"
        r"|笔触|画痕|笔痕|笔法|用笔|落笔",
        r"\b(?:marks?|strokes?) (?:from|left by|made (?:with|by)) (?:the |a |her |his )?brush\b",
        r"\b(?:describe|characteri[sz]e)\b.{0,64}\b(?:appl(?:y|ies|ied)|la(?:y|id) down|put down)"
        r"\b.{0,14}\bpaint\b",
        r"\b(?:what|how)\b.{0,18}\b(?:marks?|strokes?)\b.{0,25}\b(?:look|appear|seem)\b"
        r"|画笔(?:留下|画出|造成).{0,8}(?:痕迹|笔迹).{0,10}(?:什么样|看起来|怎样|如何)",
    ),
    "color_appearance": (_ASPECT_NOUNS["color_appearance"].pattern,),
    "line_recession": (
        r"\b(?:perspective|receding lines)\b"
        rf"|\blines\b.{{0,42}}(?:{_LINE_MOVEMENT})"
        r"|透视|线条.{0,10}(?:往里|向纵深|退向|收缩|延伸)|纵深.{0,8}线条"
        rf"|(?:线条|斜线).{{0,12}}(?:{_LINE_MOVEMENT})|(?:{_LINE_MOVEMENT}).{{0,10}}(?:线条|斜线)",
    ),
    "visual_effect": (
        r"\b(?:nervous|energy)\b|紧张",
        r"\b(?:room|scene|painting|picture|image|composition) "
        r"(?:(?:can|could|may|might) )?(?:feels?|seems?|looks?) "
        r"(?:(?:so|very|rather|somewhat|slightly|a little|a bit) )?(?:unsettling|uneasy)\b",
        r"(?:画面|房间|场景|构图).{0,12}(?:显得|看起来|让人|令人|给人).{0,6}不安",
    ),
    "background_objects": (r"\b(?:background|wallpaper|bedspread)\b|背景|墙纸|床罩",),
}
_UNSUPPORTED = {
    "paint_formula": (
        r"\b(?:what|which) (?:(?:kind|type|sort) of )?oil\b"
        r"|\b(?:paint formula|paint recipe|binder|(?:formula|recipe) for (?:the )?paint)\b"
        r"|\b(?:oil|pigments?)\b.{0,28}\b(?:mixed with|used to mix|diluted with)\b"
        r"|(?:什么|哪[种类样])油(?!画)|颜料.{0,8}配方|调色配方|黏结剂",
    ),
    "canvas_fiber": (
        r"\b(?:fibers?|fibres?|thread count|weave|linen or cotton|cotton or linen)\b"
        r"|\bwhat\b.{0,12}\b(?:canvas|fabric|cloth) (?:is |was )?(?:made of|woven from)\b"
        r"|纤维|亚麻.{0,8}棉|棉.{0,8}亚麻|(?:画布|布料).{0,12}(?:用什么织|怎么织|什么线)",
    ),
    "pigment_identity": (_PIGMENT_MATERIAL.pattern,),
    "brush_tool": (
        r"\b(?:brush (?:size|width)|(?:size|width) of (?:the |her |his |their )?brush(?:es)?"
        r"|(?:small|large|big|wide|narrow|thick|thin|flat|round|pointed|fan)"
        r"(?: or (?:small|large|big|wide|narrow|thick|thin|flat|round|pointed|fan))?"
        r" brush(?:es)?)\b(?![ -](?:marks?|strokes?|work)\b)"
        r"|(?:小|大|宽|窄|粗|细|扁平|扁头|平头|圆头|尖头|扇形)画笔"
        r"|(?:画笔.{0,6}(?:大小|尺寸|宽度|粗细|形状)|(?:什么形状的?|哪(?:一)?[种类])画笔)"
        r"|\b(?:what|which) (?:shape|type|kind)(?: of)? brush\b"
        r"|\b(?:shape|type) of (?:the |her |his |their )?brush\b",
    ),
    "vanishing_point": (
        r"\b(?:vanishing points?|convergence point|point where (?:the )?lines (?:meet|converge))\b"
        r"|\bwhere\b.{0,34}\blines\b.{0,16}\b(?:meet|converge|intersect)\b"
        r"|消失点|灭点|汇聚点|线条.{0,12}(?:交于|交汇|汇聚到)",
    ),
    "flower_species": (
        r"\b(?:what|which) (?:kind |type |sort |species )?(?:of )?flowers?\b|\bflower species\b"
        r"|\b(?:are|is|were) (?:those|these|they|that|this|the flowers?) "
        r"(?:roses?|tulips?|daisy|daisies|lily|lilies|peon(?:y|ies)|violets?|orchids?)\b"
        r"|花(?:的)?品种|花卉品种|(?:什么|哪(?:一)?种|哪些种类的)花(?:呢|吗|$)|花种"
        r"|花(?:卉|朵)?(?:是|属于)(?:什么|哪(?:一)?种)(?:呢|吗|$)"
        r"|(?:是不是|是)(?:玫瑰|月季|郁金香|雏菊|百合|牡丹|紫罗兰)(?:花|吗|呢|还是|$)",
    ),
    "painting_process": (
        r"\b(?:how (?:long|many (?:hours?|days?|strokes?|marks?|layers?|coats?))"
        r"|paint(?:ing)? (?:sequence|process)|(?:wait|before|after)\b.{0,36}\bdry(?:ing)?)\b"
        r"|\b(?:paint|layers?)\b.{0,38}\b(?:dry|dried|drying|still wet)\b"
        r"|花了多少(?:小时|天)|画了多久|先.{0,12}落笔|从哪.{0,8}落笔"
        r"|(?:颜料|涂层).{0,20}(?:干了|干透|晾干|没干|未干|还湿)",
    ),
    "artist_intent": (
        r"\b(?:intentions?|intentional(?:ly)?|intend(?:ed)?|aim(?:ed)? to"
        r"|(?:say|said|write|wrote) (?:that )?(?:he|she|they) (?:want(?:ed)?|intend(?:ed)?)"
        r"|want(?:ed)? (?:to make|viewers? to))\b"
        r"|\bwhy (?:did|does) (?:the (?:artist|painter)|van gogh|morisot|he|she) (?:choose|use|pick|prefer)\b"
        r"|意图|有意|故意|刻意|(?:说过|写过).{0,24}(?:想让|打算)|(?:画家|艺术家|梵高|莫里索).{0,14}想让",
    ),
    "source_history": (
        r"\b(?:conservation|provenance|re[- ]?lin(?:e|ed|ing)|restretch(?:ed|ing)?|restor(?:ed|ation)"
        r"|cut down|trimmed|commission(?:ed)?|who owned|when (?:was|were|did))\b"
        r"|\b(?:copy|copied|model(?:ed|led)?|inspir(?:e|ed)|bas(?:e|ed))\b.{0,50}\b(?:real|actual)\s+(?:room|wallpaper|pattern)\b"
        r"|裁(?:小|剪|过|掉|边)|重新绷|修复|修补|换过|委托|谁收藏|归谁|创作时间|什么时候(?:画|创作)"
        r"|(?:照着|照搬|仿照|临摹|取自).{0,28}(?:真实|实际|现实|实景)",
    ),
    "depicted_identity": (
        r"\b(?:who (?:is|was) (?:the |that )?(?:woman|man|figure|sitter|person)"
        r"|(?:woman|sitter|figure)[’']s (?:real )?name|(?:real )?name of the (?:woman|sitter|figure))\b"
        r"|真实姓名|画中.{0,12}(?:是谁|叫什么)|(?:女子|女人|人物|模特).{0,10}(?:是谁|叫什么)"
        r"|身后.{0,12}(?:站的是谁|是谁)",
    ),
    "exhibition_location": (
        r"\b(?:which gallery|which museum|where (?:is|can i see))\b.{0,42}"
        r"\b(?:painting|artwork|display|exhibit|today|now)\b|(?:展厅|展馆|在哪.{0,8}展出)",
    ),
    "dimensions": (
        r"\b(?:(?:actual|real|exact|physical) )?(?:measurements?|dimensions?)\b"
        r"|\bhow (?:wide|tall|large|big) (?:is|was|are|were)\b"
        r"|\b(?:actual|real|exact|physical) (?:size|width|height)\b"
        r"|尺寸|多宽|多高|多大|宽度是多少|高度是多少|实测|实际比例",
    ),
}
_BACKGROUND_OBJECTS = re.compile(
    r"\b(?:what|which)\b.{0,80}\b(?:flower(?:ed|[- ]like)?|floral|patterned)\b"
    r".{0,65}\b(?:room|scene|picture|painting|image|suggest(?:s|ed)?|look(?:s)? (?:like|as if))\b"
    r"|\b(?:room|scene|picture|painting|image)\b.{0,80}"
    r"\b(?:flower(?:ed|[- ]like)?|floral) (?:patterns?|motifs?|shapes?|designs?)\b"
    r"|(?:什么|哪些|哪种|哪件)(?:物件|物品|东西).{0,32}(?:花卉图案|花形|花纹|花卉纹样)",
    re.I,
)
_BACKGROUND_APPEARANCE = re.compile(
    r"\b(?:floral|flowers?|flower[- ]like|patterns?|shapes?|looks? like|suggest(?:s|ed)?)\b"
    r"|花形|花纹|花卉|花朵|图案|看起来像|看着像|像什么", re.I,
)
_EFFECT_REQUEST = re.compile(
    r"\b(?:what (?:(?:kind|sort|type) of )?(?:effect|feeling|mood)|which visual details)\b.{0,96}"
    r"\b(?:room|scene|painting|picture|image|composition|lines|colou?rs?|suggest|convey|feel)\b"
    r"|\b(?:what|which)\b.{0,25}\b(?:gives?|makes?|creates?)\b.{0,28}"
    r"\b(?:room|scene|painting|picture|image|composition)\b.{0,24}"
    r"\b(?:uneasy|unease|tense|tension|nervous|unsettling)\b"
    r"|(?:画面|房间|场景|构图|整幅画).{0,32}(?:给人|让人|令人|带来|传达).{0,10}"
    r"(?:什么(?:样的)?|怎样的)(?:感觉|感受|情绪|气氛|氛围|气息|观感)"
    r"|(?:色彩|线条).{0,26}什么感觉|哪些.{0,18}(?:感觉|不安|紧张)", re.I,
)
_OBJECT_REQUEST = re.compile(
    r"\b(?:what|which) (?:objects?|things?|items?)\b.{0,96}"
    r"\b(?:suggest|depict|show|represent|evoke|look like|hint(?:ed)? at)\b"
    r"|(?:联想到|暗示|表现|描绘|勾勒|画出|看起来像|看出|看见).{0,18}"
    r"(?:什么|哪些|哪几件)(?:物件|物品|东西)", re.I,
)
_SCENE_VIEW_QUESTION = re.compile(
    r"\bwhat (?:can|could|do) (?:you|we) (?:see|make out|pick out)\b", re.I,
)
_OPEN_MARK_SUGGESTION = re.compile(
    r"^\s*(?:(?:and|also)\s+)?what (?:do|does|did)\b.{0,24}\b(?:suggest|depict|represent|evoke)"
    r"(?: (?:is|was|might be) there)?\s*$"
    r"|(?:它(?:们)?|这些|那些)(?:让人)?(?:联想到|暗示(?:了)?|描绘(?:了)?)什么\s*$", re.I,
)
_LOCATIVE_PREFIX = re.compile(
    r"^\s*(?:in|on|for|about|regarding|as for|looking at)\b|^\s*(?:在|对于|关于|说到|就)",
    re.I,
)
_DEICTIC_TOPIC = re.compile(
    r"^\s*(?:眼前)?(?:这|那)[幅张件](?:画|作品)?(?:呢)?\s*$"
    r"|^\s*(?:this|that) (?:one|artwork|painting|picture|work)(?: here)?\s*$",
    re.I,
)
_TEMPORAL_PREFIX = re.compile(r"^\s*(?:when|while|as) (?:i|we|you|he|she|they)\b", re.I)
_MEDIUM_COORDINATION = re.compile(
    r"\b(?:what|which)\b.{0,56}\b(?:medium|materials?)\b.{0,24}\b(?:and|or|along with)\b"
    r".{0,28}\b(?:actual |exact )?(?:dimensions?|measurements?|size)\b"
    r"|\b(?:what|which)\b.{0,32}\b(?:dimensions?|measurements?|size)\b.{0,24}\b(?:and|or|along with)\b"
    r".{0,24}\b(?:medium|materials?)\b|(?:材质|媒介|材料).{0,8}(?:和|与|以及).{0,8}尺寸"
    r"|尺寸.{0,8}(?:和|与|以及).{0,8}(?:材质|媒介|材料)", re.I,
)
_PAINT_COMPOUND = re.compile(r"\b(?:paint|painted)\b.{0,34}\bwith\b.{0,12}\band\b.{0,10}\bon\b", re.I)


def _compile_rules(rules):
    return {
        facet: tuple(re.compile(pattern, re.I) for pattern in patterns)
        for facet, patterns in rules.items()
    }


_SUPPORTED_PATTERNS = _compile_rules(_SUPPORTED)
_UNSUPPORTED_PATTERNS = _compile_rules(_UNSUPPORTED)


def _split_spans(pattern, text, start, end):
    for separator in pattern.finditer(text, start, end):
        yield start, separator.start()
        start = separator.end()
    yield start, end


def _parts(text):
    start = 0
    for boundary in (*_SENTENCE_END.finditer(text), None):
        end = len(text) if boundary is None else boundary.start()
        question_end = boundary is not None and "?" in boundary.group()
        pieces = list(_split_spans(_RESTART, text, start, end))
        for piece_index, (left, right) in enumerate(pieces):
            commas = list(_split_spans(_COMMA, text, left, right))
            comma_requests = [bool(_QUESTION.search(text[a:b])) for a, b in commas]
            prefix = text[commas[0][0]:commas[0][1]]
            topic_prefix = _DEICTIC_TOPIC.fullmatch(prefix) or (
                not comma_requests[0] and _LOCATIVE_PREFIX.search(prefix)
            )
            if len(commas) > 1 and topic_prefix and all(comma_requests[1:]):
                # Keep the scope preface attached to the first request.
                subparts = [(commas[0][0], commas[1][1]), *commas[2:]]
            elif len(commas) > 1 and all(comma_requests):
                subparts = commas
            else:
                subparts = [(left, right)]
            for index, (a, b) in enumerate(subparts):
                while a < b and text[a] in " ,，:：()":
                    a += 1
                while a < b and text[b - 1] in " ,，:：()":
                    b -= 1
                if a < b:
                    last = (left, right) == pieces[-1] and index == len(subparts) - 1
                    yield a, b, question_end and last, piece_index > 0 or index > 0
        if boundary is not None:
            start = boundary.end()


def _is_request(text, question_mark):
    prefix, comma, body = text.partition(",")
    if comma and _TEMPORAL_PREFIX.search(prefix):
        return bool(_QUESTION.search(body) or _COMMAND.search(body))
    if question_mark or _QUESTION.search(text) or _COMMAND.search(text) or text.lower() == "made of":
        return True
    if comma and _LOCATIVE_PREFIX.search(prefix) and (_QUESTION.search(body) or _COMMAND.search(body)):
        return True
    words = re.findall(r"\w+", text.lower())
    return bool(words) and (
        all(word in _BARE_ENGLISH for word in words)
        or _BARE_CHINESE.fullmatch(" ".join(words)) is not None
    )


def _facets(text, visual_marks_context, inherited_aspect=None):
    unsupported = {
        facet for facet, patterns in _UNSUPPORTED_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }
    if _PALETTE_COMPOSITION.search(text) or (
        inherited_aspect == "color_appearance" and _REFERENT_COMPOSITION.search(text)
    ):
        unsupported.add("pigment_identity")
    if "paint_formula" in unsupported:
        unsupported.discard("pigment_identity")
    if "brush_tool" in unsupported:
        unsupported.discard("dimensions")

    if unsupported:
        if "dimensions" in unsupported and _MEDIUM_COORDINATION.search(text):
            return ("medium", *(facet for facet in _UNSUPPORTED if facet in unsupported))
        return tuple(facet for facet in _UNSUPPORTED if facet in unsupported)

    supported = {
        facet for facet, patterns in _SUPPORTED_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }
    if _PAINT_AND_SUPPORT.search(text) or _PAINT_COMPOUND.search(text):
        supported.update(("paint_medium", "painting_support"))
    if _MEDIUM_CHECK.search(text) and not {"paint_medium", "painting_support"} <= supported:
        supported.difference_update(("paint_medium", "painting_support"))
        supported.add("medium")
    elif supported.intersection(("paint_medium", "painting_support")):
        supported.discard("medium")

    if _MARKS.search(text) and _APPEARANCE.search(text) and (
        _BEHIND.search(text) or visual_marks_context or _COMPARISON.search(text)
    ):
        supported.add("brushwork_appearance")
    if _BACKGROUND_OBJECTS.search(text) or (
        _BEHIND.search(text) and (_BACKGROUND_APPEARANCE.search(text) or _SCENE_VIEW_QUESTION.search(text))
    ):
        supported.add("background_objects")
    if _OBJECT_REQUEST.search(text) or (visual_marks_context and _MARKS.search(text) and _MARK_SUGGESTION.search(text)):
        supported.add("background_objects")
        supported.discard("brushwork_appearance")
    elif _STROKE_DESCRIPTION.search(text):
        supported.add("brushwork_appearance")
        supported.discard("background_objects")
    if _EFFECT_REQUEST.search(text):
        supported.add("visual_effect")
        supported.difference_update(("color_appearance", "line_recession", "brushwork_appearance"))

    if not supported and inherited_aspect:
        if inherited_aspect == "brushwork_appearance" and _MARK_SUGGESTION.search(text):
            supported.add("background_objects")
        else:
            supported.add(inherited_aspect)
    return tuple(facet for facet in _SUPPORTED if facet in supported) or ("unknown",)


def _references(text):
    matches = (
        (match.start(), artwork_id)
        for artwork_id, pattern in _ARTWORK_REFERENCES.items()
        for match in pattern.finditer(text)
    )
    return tuple(dict.fromkeys(artwork_id for _, artwork_id in sorted(matches)))


def _mentioned_aspects(text):
    return {aspect for aspect, pattern in _ASPECT_NOUNS.items() if pattern.search(text)}


def _inherited_aspect(text, mentioned, earlier, recent):
    if not _ASPECT_REFERENCE.search(text):
        return None
    if "line_recession" in earlier and _RECESSION.search(text):
        return "line_recession"
    if "brushwork_appearance" in earlier and (
        _OBJECT_REQUEST.search(text) or _OPEN_MARK_SUGGESTION.search(text)
    ):
        return "brushwork_appearance"
    if not mentioned and len(recent) == 1:
        aspect = next(iter(recent))
        if _DESCRIPTION_OF_REFERENCE.search(text) or (
            aspect == "color_appearance" and _REFERENT_COMPOSITION.search(text)
        ):
            return aspect
    return None


def parse_requests(query: str, artwork_id: str) -> tuple[str, tuple[QueryRequest, ...]]:
    """Return case-preserving normalized text and its scoped fact requests."""
    normalized = " ".join(unicodedata.normalize("NFKC", query).split())
    requests = []
    referenced_artwork = artwork_id
    earlier_aspects = set()
    recent_aspects = set()
    has_scene = False
    for start, end, question_mark, continued in _parts(normalized):
        text = normalized[start:end]
        references = _references(text)
        current_reference = _CURRENT_REFERENCE.search(text)
        is_request = _is_request(text, question_mark)
        scene_mentioned = bool(_BEHIND.search(text) or _SCENE_SETTING.search(text))
        mentioned = _mentioned_aspects(text)
        inherited = _inherited_aspect(text, mentioned, earlier_aspects, recent_aspects)
        refers_to_scene = _REFERRED_MARKS.search(text) or _REFERRED_PATTERN.search(text)
        comparison = _COMPARISON.search(text) or (
            (len(references) > 1 or bool(references and current_reference))
            and _SAME_ATTRIBUTE.search(text)
        )
        if not references and _BOTH_WORKS.search(text):
            targets = (_BEDROOM, _TOILETTE)
        elif comparison and references:
            targets = tuple(dict.fromkeys((artwork_id, *references))) if current_reference else references
        elif references:
            targets = (references[0],)
        elif current_reference:
            targets = (artwork_id,)
        elif (
            not is_request or inherited or (has_scene and refers_to_scene)
            or continued or _DEPENDENT_REFERENCE.search(text)
        ):
            targets = (referenced_artwork,)
        else:
            targets = (artwork_id,)

        if len(targets) != 1 or targets[0] != referenced_artwork:
            earlier_aspects.clear()
            recent_aspects.clear()
            has_scene = False
            inherited = None

        facets = ()
        if is_request:
            facets = _facets(text, bool(has_scene and refers_to_scene), inherited)
            for target in targets:
                for facet in facets:
                    requests.append(QueryRequest(start, end, target, facet))

        if len(targets) == 1:
            referenced_artwork = targets[0]
            has_scene = scene_mentioned or "background_objects" in facets
            requested = set(facets) & _ASPECT_NOUNS.keys()
            earlier_aspects.update(mentioned | requested)
            recent_aspects = requested or mentioned
        else:
            # A comparison cannot establish a single antecedent for the next part.
            referenced_artwork = artwork_id

    return normalized, tuple(requests)
