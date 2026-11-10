#!/usr/bin/env python3
"""程序化生成本地 SVG 占位视觉，零外链、零网络请求、零生成调用。

每个主题是一张抽象 AI 作品缩略图：渐变底 + 体积光 + 主体几何 + 噪点。
输出统一 600x600，页面里用 object-fit: cover 适配不同画幅。
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)


def grad(gid, c1, c2, angle=135):
    x1, y1, x2, y2 = [(0, 0, 100, 100), (0, 100, 100, 0), (0, 0, 0, 100), (100, 0, 0, 100)][angle // 45 % 4]
    return (f'<linearGradient id="{gid}" x1="{x1}%" y1="{y1}%" x2="{x2}%" y2="{y2}%">'
            f'<stop offset="0%" stop-color="{c1}"/><stop offset="100%" stop-color="{c2}"/></linearGradient>')


def glow(gid, color, opacity=0.75):
    return (f'<radialGradient id="{gid}"><stop offset="0%" stop-color="{color}" stop-opacity="{opacity}"/>'
            f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/></radialGradient>')


MOTIFS = {
    "product": """
      <ellipse cx="300" cy="470" rx="200" ry="34" fill="#000" opacity=".28"/>
      <path d="M0 430 Q150 398 300 424 T600 416 V600 H0Z" fill="#20140c" opacity=".55"/>
      <rect x="252" y="196" width="96" height="238" rx="12" fill="url(#obj)" opacity=".95"/>
      <rect x="272" y="168" width="56" height="40" rx="8" fill="#f3d9a4" opacity=".9"/>
      <rect x="286" y="146" width="28" height="28" rx="6" fill="#fff0cf" opacity=".95"/>
      <rect x="264" y="238" width="72" height="112" rx="4" fill="#fff" opacity=".13"/>
      <path d="M252 300 Q300 288 348 300" stroke="#fff3d6" stroke-width="2" opacity=".45" fill="none"/>
      <text x="300" y="346" text-anchor="middle" font-family="Georgia,serif" font-size="17" letter-spacing="5" fill="#fff4dd" opacity=".85">AURUM</text>
      <circle cx="168" cy="188" r="7" fill="#ffe9b5" opacity=".7"/>
      <circle cx="436" cy="146" r="4" fill="#ffe9b5" opacity=".6"/>
    """,
    "portrait": """
      <ellipse cx="300" cy="250" rx="152" ry="176" fill="url(#obj)" opacity=".9"/>
      <path d="M148 250 Q142 96 300 88 Q458 96 452 250 Q444 172 300 166 Q156 172 148 250Z" fill="#211a1d" opacity=".9"/>
      <path d="M156 246 Q150 400 200 430 L400 430 Q450 400 444 246 Q430 336 300 340 Q170 336 156 246Z" fill="#2a2024" opacity=".72"/>
      <path d="M300 340 Q256 420 214 600 H386 Q344 420 300 340Z" fill="#f0d9d2" opacity=".72"/>
      <ellipse cx="300" cy="258" rx="96" ry="118" fill="#f7e4db" opacity=".97"/>
      <path d="M204 232 Q208 142 300 138 Q392 142 396 232 Q370 180 300 178 Q230 180 204 232Z" fill="#241b1f" opacity=".95"/>
      <path d="M262 262 Q276 252 290 262 M310 262 Q324 252 338 262" stroke="#3c2c2e" stroke-width="4" fill="none" stroke-linecap="round"/>
      <path d="M282 316 Q300 326 318 316" stroke="#c9777a" stroke-width="5" fill="none" stroke-linecap="round"/>
      <circle cx="392" cy="272" r="13" fill="none" stroke="#fff4cf" stroke-width="4" opacity=".95"/>
      <circle cx="392" cy="272" r="4" fill="#fffbe9"/>
      <text x="60" y="556" font-family="Helvetica,Arial,sans-serif" font-size="19" letter-spacing="8" fill="#4a3338" opacity=".75">LUMIERE</text>
    """,
    "headphone": """
      <g opacity=".93">
        <path d="M170 336 V274 A130 130 0 0 1 430 274 V336" fill="none" stroke="url(#obj)" stroke-width="30" stroke-linecap="round"/>
        <rect x="142" y="310" width="66" height="146" rx="30" fill="#15181e" stroke="#5d6674" stroke-width="3"/>
        <rect x="392" y="310" width="66" height="146" rx="30" fill="#15181e" stroke="#5d6674" stroke-width="3"/>
        <rect x="158" y="330" width="34" height="106" rx="17" fill="url(#obj)" opacity=".65"/>
        <rect x="408" y="330" width="34" height="106" rx="17" fill="url(#obj)" opacity=".65"/>
      </g>
      <text x="300" y="132" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-weight="700" font-size="76" letter-spacing="10" fill="#e8edf5" opacity=".13">ZERO</text>
      <text x="300" y="524" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="16" letter-spacing="9" fill="#c7d2e0" opacity=".6">SILENCE, REDEFINED</text>
      <rect x="56" y="56" width="42" height="2" fill="#9ba9bb" opacity=".5"/>
      <rect x="56" y="56" width="2" height="42" fill="#9ba9bb" opacity=".5"/>
    """,
    "field": """
      <path d="M0 330 Q150 296 300 322 T600 306 V600 H0Z" fill="#5d8a68" opacity=".55"/>
      <path d="M0 386 Q160 350 300 380 T600 366 V600 H0Z" fill="#3f6b4f" opacity=".7"/>
      <path d="M0 462 Q180 420 340 456 T600 442 V600 H0Z" fill="#2f5540" opacity=".85"/>
      <g opacity=".95">
        <circle cx="316" cy="292" r="22" fill="#f0d6bd"/>
        <path d="M292 286 Q300 258 328 268 L344 276 Q326 276 292 292Z" fill="#2f3b44"/>
        <path d="M316 314 Q286 326 274 420 L356 420 Q344 326 316 314Z" fill="#e8eef2"/>
        <path d="M274 420 Q268 486 262 560 L300 560 L312 420Z" fill="#46566a"/>
        <path d="M356 420 Q362 486 368 560 L330 560 L320 420Z" fill="#46566a"/>
        <path d="M274 344 Q236 366 226 400" stroke="#f0d6bd" stroke-width="13" fill="none" stroke-linecap="round"/>
        <path d="M356 344 Q392 358 404 392" stroke="#f0d6bd" stroke-width="13" fill="none" stroke-linecap="round"/>
      </g>
      <circle cx="118" cy="132" r="62" fill="url(#light)" opacity=".7"/>
      <path d="M0 210 Q120 190 240 214" stroke="#fff" stroke-width="2" opacity=".18" fill="none"/>
    """,
    "burger": """
      <ellipse cx="300" cy="488" rx="196" ry="30" fill="#000" opacity=".4"/>
      <ellipse cx="300" cy="228" rx="172" ry="72" fill="url(#obj)"/>
      <path d="M128 228 Q300 150 472 228 Q300 268 128 228Z" fill="#f0ae4c"/>
      <g fill="#fff3c4" opacity=".85">
        <ellipse cx="220" cy="206" rx="7" ry="3"/><ellipse cx="286" cy="190" rx="7" ry="3"/>
        <ellipse cx="356" cy="202" rx="7" ry="3"/><ellipse cx="404" cy="218" rx="6" ry="3"/>
        <ellipse cx="256" cy="222" rx="6" ry="3"/>
      </g>
      <path d="M126 246 Q300 224 474 246 L470 274 Q300 296 130 274Z" fill="#5c9e48"/>
      <rect x="132" y="272" width="336" height="42" rx="14" fill="#6b3418"/>
      <rect x="128" y="310" width="344" height="20" rx="8" fill="#f6c44f"/>
      <rect x="136" y="330" width="328" height="46" rx="12" fill="#71381c"/>
      <path d="M142 376 Q300 404 458 376 L452 414 Q300 448 148 414Z" fill="#df8a37"/>
      <path d="M300 116 Q286 150 300 166" stroke="#fff" stroke-width="5" opacity=".22" fill="none" stroke-linecap="round"/>
      <path d="M240 112 Q228 146 240 160 M362 112 Q350 146 362 160" stroke="#fff" stroke-width="4" opacity=".16" fill="none" stroke-linecap="round"/>
      <text x="300" y="558" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-weight="700" font-size="24" letter-spacing="7" fill="#ffd79b" opacity=".8">FLAME &amp; STACK</text>
    """,
    "fashion": """
      <rect x="52" y="72" width="496" height="456" fill="none" stroke="#fff" stroke-width="1.5" opacity=".28"/>
      <text x="300" y="182" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-weight="700" font-size="82" letter-spacing="6" fill="#fff" opacity=".16">DREAMNA</text>
      <g>
        <circle cx="300" cy="244" r="28" fill="#e8cbb9"/>
        <path d="M272 236 Q276 206 300 206 Q324 206 328 236 L322 226 Q300 216 278 226Z" fill="#27242b"/>
        <path d="M300 272 Q262 284 252 356 L268 460 L332 460 L348 356 Q338 284 300 272Z" fill="#1d202a"/>
        <path d="M252 356 Q214 372 206 410 L216 418 Q232 384 258 378Z" fill="#1d202a"/>
        <path d="M348 356 Q386 372 394 410 L384 418 Q368 384 342 378Z" fill="#1d202a"/>
        <path d="M268 460 L262 552 L288 552 L296 460Z M332 460 L338 552 L312 552 L304 460Z" fill="#2c3140"/>
        <rect x="264" y="322" width="72" height="6" fill="#e8cbb9" opacity=".5"/>
      </g>
      <rect x="386" y="366" width="104" height="42" rx="21" fill="#fff" opacity=".16"/>
      <circle cx="412" cy="387" r="9" fill="#fff" opacity=".7"/>
      <text x="432" y="392" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#fff" opacity=".8">NEW LOOK</text>
    """,
    "abstract": """
      <path d="M-40 420 Q120 260 260 380 T640 300 V640 H-40Z" fill="url(#obj)" opacity=".85"/>
      <path d="M-40 470 Q140 330 280 440 T640 360 V640 H-40Z" fill="#fff" opacity=".1"/>
      <path d="M-40 520 Q160 400 300 500 T640 420 V640 H-40Z" fill="#0b1020" opacity=".35"/>
      <circle cx="404" cy="174" r="82" fill="url(#light)"/>
      <circle cx="404" cy="174" r="52" fill="#fff" opacity=".14"/>
      <g stroke="#fff" stroke-width="1" fill="none" opacity=".24">
        <circle cx="404" cy="174" r="104"/><circle cx="404" cy="174" r="140"/><circle cx="404" cy="174" r="176"/>
      </g>
    """,
    "zen": """
      <circle cx="300" cy="300" r="168" fill="none" stroke="#fff" stroke-width="2" opacity=".35"/>
      <circle cx="300" cy="300" r="128" fill="url(#obj)" opacity=".8"/>
      <path d="M0 452 Q150 420 300 452 T600 452 V600 H0Z" fill="#e7dece" opacity=".2"/>
      <path d="M0 486 Q150 456 300 486 T600 486" stroke="#fff" stroke-width="1.4" fill="none" opacity=".3"/>
      <path d="M0 516 Q150 488 300 516 T600 516" stroke="#fff" stroke-width="1.4" fill="none" opacity=".18"/>
      <rect x="292" y="104" width="16" height="52" rx="8" fill="#fff" opacity=".55"/>
    """,
    "neon": """
      <g stroke="url(#obj)" stroke-width="3" fill="none" opacity=".85">
        <path d="M60 600 V300 H140 V420 H200 V250 H270 V600"/>
        <path d="M330 600 V210 H400 V340 H460 V280 H540 V600"/>
        <path d="M0 470 H600 M0 520 H600"/>
      </g>
      <g fill="#7ef0e0" opacity=".55">
        <rect x="78" y="326" width="12" height="18"/><rect x="110" y="326" width="12" height="18"/>
        <rect x="216" y="286" width="12" height="18"/><rect x="352" y="246" width="12" height="18"/>
        <rect x="418" y="372" width="12" height="18"/><rect x="486" y="316" width="12" height="18"/>
      </g>
      <circle cx="300" cy="158" r="86" fill="url(#light)"/>
      <circle cx="300" cy="158" r="44" fill="#ff7bc8" opacity=".55"/>
    """,
    "ocean": """
      <g opacity=".9">
        <ellipse cx="300" cy="212" rx="88" ry="66" fill="url(#obj)"/>
        <path d="M212 212 Q300 250 388 212" stroke="#fff" stroke-width="3" fill="none" opacity=".5"/>
        <g stroke="#dff6ff" stroke-width="7" fill="none" stroke-linecap="round" opacity=".65">
          <path d="M244 258 Q230 350 254 440"/><path d="M276 264 Q268 366 282 470"/>
          <path d="M324 264 Q336 366 320 470"/><path d="M356 258 Q372 350 348 440"/>
        </g>
      </g>
      <g fill="#fff" opacity=".3">
        <circle cx="140" cy="150" r="7"/><circle cx="452" cy="112" r="5"/><circle cx="486" cy="330" r="9"/>
        <circle cx="112" cy="392" r="4"/><circle cx="404" cy="474" r="5"/>
      </g>
      <path d="M0 0 H600 V600 H0Z" fill="url(#deep)"/>
    """,
    "cat": """
      <path d="M168 600 Q168 382 300 382 Q432 382 432 600Z" fill="url(#obj)"/>
      <path d="M196 262 L188 148 L262 208 Q300 194 338 208 L412 148 L404 262 Q420 330 300 350 Q180 330 196 262Z" fill="#2b2320"/>
      <ellipse cx="300" cy="286" rx="104" ry="88" fill="#3b302a"/>
      <ellipse cx="256" cy="272" rx="15" ry="21" fill="#f5c65a"/>
      <ellipse cx="344" cy="272" rx="15" ry="21" fill="#f5c65a"/>
      <path d="M256 262 V282 M344 262 V282" stroke="#231c18" stroke-width="5"/>
      <path d="M288 316 L312 316 L300 328Z" fill="#e79c9c"/>
      <path d="M300 330 Q284 344 268 336 M300 330 Q316 344 332 336" stroke="#e8d8c8" stroke-width="3" fill="none"/>
      <g stroke="#e8d8c8" stroke-width="2" opacity=".7" fill="none">
        <path d="M212 300 L148 286 M212 314 L146 316 M388 300 L452 286 M388 314 L454 316"/>
      </g>
      <path d="M300 350 Q212 396 208 600 H392 Q388 396 300 350Z" fill="#8d5b3f" opacity=".55"/>
    """,
    "space": """
      <circle cx="392" cy="214" r="126" fill="url(#obj)"/>
      <ellipse cx="392" cy="214" rx="186" ry="40" fill="none" stroke="#ffd0e6" stroke-width="10" opacity=".45" transform="rotate(-18 392 214)"/>
      <path d="M0 470 Q160 420 300 462 T600 440 V600 H0Z" fill="#20132f" opacity=".8"/>
      <g transform="translate(150 300) rotate(-12)">
        <path d="M0 30 L92 0 L150 30 L92 60Z" fill="#dfe9ff" opacity=".9"/>
        <path d="M92 0 L92 60 L150 30Z" fill="#8fa8d8" opacity=".8"/>
        <circle cx="42" cy="30" r="9" fill="#7ee6ff"/>
        <path d="M0 30 L-52 12 L-52 48Z" fill="#ff9ed2" opacity=".8"/>
      </g>
      <g fill="#fff" opacity=".65">
        <circle cx="90" cy="110" r="2.5"/><circle cx="212" cy="76" r="2"/><circle cx="510" cy="112" r="3"/>
        <circle cx="548" cy="300" r="2"/><circle cx="72" cy="408" r="2"/><circle cx="470" cy="524" r="2"/>
      </g>
    """,
}

THEMES = [
    ("art-01", "商品图场景合成", "#3a2413", "#a8702f", "#ffd79b", "#ffe4ae", "product", 135),
    ("art-02", "镜面珠宝人像", "#f3ded8", "#c99098", "#f6d9c9", "#fff0e6", "portrait", 45),
    ("art-03", "极简耳机海报", "#171b23", "#3a4658", "#6d829e", "#b9c9e0", "headphone", 135),
    ("art-04", "日系青春短片", "#9fc4a6", "#3d7356", "#bfe3c4", "#f6ffd9", "field", 90),
    ("art-05", "高燃汉堡宣传", "#2a1008", "#c24a12", "#ff9a3c", "#ffdf9a", "burger", 135),
    ("art-06", "时尚换装快切", "#3b3d55", "#9297b8", "#b9aecb", "#e8dcff", "fashion", 45),
    ("art-07", "月光下的花园", "#101b30", "#36557c", "#6f8fd0", "#c8e0ff", "abstract", 135),
    ("art-08", "东方极简", "#d9cfbd", "#8b7f6d", "#e8dcc4", "#fff6e4", "zen", 45),
    ("art-09", "赛博城市夜景", "#0b1124", "#26346b", "#7ef0e0", "#ff7bc8", "neon", 135),
    ("art-10", "水母在海底遨游", "#06243d", "#0b5c7a", "#5fd8e8", "#c6f4ff", "ocean", 90),
    ("art-11", "文艺复兴猫咪", "#3a2a1c", "#8c5a35", "#f5c65a", "#ffe6b0", "cat", 135),
    ("art-12", "粉色星球飞行器", "#241235", "#6b3478", "#ff9ed2", "#ffd6ef", "space", 45),
]


def build(theme):
    key, name, c1, c2, obj1, light, motif, angle = theme
    defs = (grad("bg", c1, c2, angle) + grad("obj", obj1, light, 45)
            + glow("light", light, 0.55) + glow("halo", c1, 0.9)
            + '<radialGradient id="deep"><stop offset="0%" stop-color="#0b5c7a" stop-opacity="0"/>'
              '<stop offset="100%" stop-color="#041a2e" stop-opacity=".75"/></radialGradient>'
            + '<filter id="grain"><feTurbulence type="fractalNoise" baseFrequency=".85" numOctaves="3" stitchTiles="stitch"/>'
              '<feColorMatrix type="saturate" values="0"/><feComponentTransfer>'
              '<feFuncA type="linear" slope=".14"/></feComponentTransfer></filter>'
            + '<filter id="soft"><feGaussianBlur stdDeviation="34"/></filter>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 600" width="600" height="600" '
            f'role="img" aria-label="{name}">'
            f'<defs>{defs}</defs>'
            f'<rect width="600" height="600" fill="url(#bg)"/>'
            f'<ellipse cx="140" cy="120" rx="230" ry="200" fill="url(#light)" opacity=".55" filter="url(#soft)"/>'
            f'<ellipse cx="500" cy="520" rx="230" ry="200" fill="url(#halo)" opacity=".5" filter="url(#soft)"/>'
            f'{MOTIFS[motif]}'
            f'<rect width="600" height="600" filter="url(#grain)" opacity=".5" style="mix-blend-mode:overlay"/>'
            f'</svg>')


def main():
    for theme in THEMES:
        (OUT / f"{theme[0]}.svg").write_text(build(theme), encoding="utf-8")

    logo = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" width="40" height="40">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0%" stop-color="#4be1ff"/><stop offset="45%" stop-color="#7c6bff"/>'
            '<stop offset="100%" stop-color="#ff6bc4"/></linearGradient></defs>'
            '<path d="M20 3 L23.5 16.5 L37 20 L23.5 23.5 L20 37 L16.5 23.5 L3 20 L16.5 16.5Z" fill="url(#g)"/>'
            '<circle cx="30.5" cy="9.5" r="3" fill="#ffe27a"/></svg>')
    (OUT / "logo.svg").write_text(logo, encoding="utf-8")

    avatar = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
              '<defs><linearGradient id="a" x1="0" y1="0" x2="1" y2="1">'
              '<stop offset="0%" stop-color="#ffd6b0"/><stop offset="100%" stop-color="#e58fa8"/></linearGradient></defs>'
              '<circle cx="32" cy="32" r="32" fill="url(#a)"/>'
              '<circle cx="32" cy="26" r="11" fill="#fff3e6" opacity=".92"/>'
              '<path d="M11 60 Q13 41 32 41 Q51 41 53 60Z" fill="#fff3e6" opacity=".92"/>'
              '<path d="M20 20 Q22 9 32 9 Q42 9 44 20 Q38 15 32 15 Q26 15 20 20Z" fill="#4b3540"/></svg>')
    (OUT / "avatar.svg").write_text(avatar, encoding="utf-8")
    print(f"generated {len(THEMES)} artworks + logo + avatar -> {OUT}")


if __name__ == "__main__":
    main()
