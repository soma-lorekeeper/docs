# 예시 프로젝트 이미지 프롬프트

> 작성일: 2026-11-12
> 대상: 예시 프로젝트 「별이 없는 세자」의 캐릭터·아이템 이미지를 다시 만들거나 더하는 사람
> 이미지 파일: content `src/main/resources/sample/images/{key}.webp`

OpenAI `gpt-image-2`로 `1024x1024`, `quality: high`로 만든 뒤 `cwebp -q 86 -resize 512 512`로 줄였다. 에디터의 이미지 자르기 창이 저장하는 형식(512×512 WebP)과 같다. 프롬프트는 문서마다 다른 묘사 뒤에 아래 공통 화풍 문장을 붙인다.

## 공통 화풍

```
Korean romance-fantasy webtoon illustration, 2D digital painting, clean fine lineart, soft cel shading with gentle gradients, luminous highlights, elegant and beautiful character design, alternate-history Joseon dynasty setting with a celestial star-magic theme, palette of deep indigo night, silver starlight and warm candle gold. Square composition for a circular avatar crop: the subject is centered with a comfortable margin, nothing important near the corners. Simple softly blurred background. No text, no letters, no watermark, no frame, no border.
```

캐릭터 프롬프트는 맨 앞에 다음 문장을 붙인다.

```
Head-and-shoulders portrait facing slightly to the side, face in the exact center of the image.
```

## 문서별 묘사

### 윤서화 (`c-seohwa`)

```
Yun Seohwa, a 19-year-old Korean woman who is a court astronomer of the Joseon royal observatory. Calm, guarded, intelligent eyes with a faint silver glint, a small determined mouth. Long black hair tied in a low chignon held by a slender silver hairpin engraved with a flowing Milky Way pattern. Modest deep indigo hanbok jacket with a pale collar. Background: night sky full of soft stars with faint silver threads of light.
```

### 이현 (`c-hyeon`)

```
Crown Prince Yi Hyeon, a 22-year-old handsome Korean man. Languid, slightly cynical half-smile but lonely, kind eyes. Wears a deep crimson royal robe with a gold dragon roundel on the chest and a black ikseongwan royal cap. Twirls a calligraphy brush between his fingers. Background: night sky with stars, and above his head the sky is noticeably empty and dark, as if his own star is missing.
```

### 민도윤 (`c-doyun`)

```
Min Doyun, a 24-year-old Korean royal guard officer of the crown prince's guard. Stern, loyal, sharp eyes, short-tempered but warm underneath. Black military officer's robe with a dark red inner lining and a black wide-brimmed gat hat with a bead string, a sheathed sword hilt visible at his shoulder. Background: palace eaves at night with lanterns.
```

### 정시후 (`c-sihoo`)

```
Jeong Sihoo, a 20-year-old gentle Korean scholar, an astronomy student at the royal observatory. Soft, kind face with a faintly troubled, guilty look in his eyes. Light gray-blue scholar's robe and a black gat hat. He holds a small paper lantern that lights his face warmly. Background: dusky observatory courtyard.
```

### 허태정 (`c-heo`)

```
Heo Taejeong, a 52-year-old Korean high official who controls the royal observatory. Cold, calculating eyes, thin composed smile, neatly trimmed gray-streaked beard. Dark navy official's robe with a crane rank badge and a black samo official hat. Menacing dignity. Background: dim office lit by a single candle, star charts faintly behind him.
```

### 대비 심씨 (`c-daebi`)

```
Queen Dowager Sim, an elegant Korean woman in her early fifties. Gracious smile that does not reach her cold eyes. Formal deep purple dangui jacket with gold-leaf patterns, an ornate royal hairstyle with jeweled hairpins. She holds a small celadon teacup. Background: palace hall with chrysanthemum flowers.
```

### 덕이 (`c-deoki`)

```
Deoki, a 17-year-old Korean maidservant, lively and loud but warm-hearted, mid-scolding with puffed cheeks and worried eyebrows. Plain faded pink and cream cotton hanbok, a single long black braid with a red ribbon. Background: thatched-roof cottage kitchen with warm firelight.
```

### 흑요석 성반 (`i-seongban`)

```
A palm-sized round disc of polished black obsidian, centered, with the twenty-eight lunar mansion constellations inlaid as fine glowing silver thread lines and dots. It rests on folded dark blue cloth and faintly reflects a starry night sky. Mystical silver glow.
```

### 세자의 일기 (`i-diary`)

```
Still life of an object only, no people, no hands, no figures. An old Korean hand-stitched book bound with red thread in the five-hole Joseon binding style, centered, lying open on a low wooden desk beside an ink stone and a brush. Its pages are filled with blurred brush strokes that cannot be read, and the lower edge of the paper is lightly singed by fire. Warm candlelight.
```

### 명부 (`i-myeongbu`)

```
Still life of an object only, no people, no hands, no figures. A large antique Joseon registry book of star records, centered, open on a dark wooden shelf in a dim archive. Neat columns of faint unreadable brush writing fill the pages, and one cell is scraped away so the paper is thin and blank. Moonlight from a high window, dust in the air, mysterious mood.
```

### 은하잠 (`i-binyeo`)

```
A slender antique Korean silver hairpin, centered and placed diagonally, engraved along its shaft with a flowing Milky Way star pattern. Its decorative head is twisted open to reveal a tiny hollow tube with a thin folded slip of paper peeking out. Resting on deep indigo silk with soft starlight glints.
```

## 다시 만들 때

- 아이템 프롬프트에 "Still life of an object only, no people, no hands, no figures."를 넣지 않으면 사람이 함께 그려진다(세자의 일기·명부에서 겪음).
- 「흑요석 성반」은 검은 돌이어야 한다. 30화의 "백옥 성반"은 설정 오류 점검용으로 심어 둔 실수다.
- 얼굴과 물건이 가운데에 와야 관계도의 원형 노드에서 잘리지 않는다.
- 파일을 바꾸면 content 시드(`starless-prince.json`)의 `image` 값과 파일 이름을 맞춘다. 새로 만든 예시 프로젝트부터 반영된다.
