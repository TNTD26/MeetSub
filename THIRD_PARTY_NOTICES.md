# Third-party notices

MeetSub is built on open-source components and optional model weights. This
file is a convenience summary, not legal advice. The license terms shipped with
each component/model prevail.

## Runtime libraries

| Component | License | Project |
| --- | --- | --- |
| PySide6 / Qt for Python (Essentials) | LGPL-3.0 (with Qt LGPL exceptions) | https://pypi.org/project/PySide6-Essentials/ |
| faster-whisper | MIT | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | MIT | https://github.com/OpenNMT/CTranslate2 |
| Whisper (model code) | MIT | https://github.com/openai/whisper |
| PyAudioWPatch | MIT | https://github.com/s0d3s/PyAudioWPatch |
| webrtcvad-wheels / WebRTC VAD | BSD-style | https://github.com/wiseman/py-webrtcvad |
| soxr / libsoxr | LGPL-2.1 | https://github.com/dofuuz/python-soxr |
| NumPy | BSD-3-Clause | https://numpy.org/ |
| httpx | BSD-3-Clause | https://www.python-httpx.org/ |
| tokenizers | Apache-2.0 | https://github.com/huggingface/tokenizers |
| SentencePiece | Apache-2.0 | https://github.com/google/sentencepiece |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| PyAV (av) | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| OpenCC Python | Apache-2.0 | https://github.com/yichen0831/opencc-python |

## Optional/default model weights

| Model | License | Source |
| --- | --- | --- |
| `Systran/faster-whisper-small.en` | MIT (OpenAI Whisper weights) | https://huggingface.co/Systran/faster-whisper-small.en |
| `gn64/M2M100_418M_CTranslate2` | MIT (base: `facebook/m2m100_418M`) | https://huggingface.co/gn64/M2M100_418M_CTranslate2 |
| `jncraton/Qwen2.5-1.5B-Instruct-ct2-int8` | Apache-2.0 (base: `Qwen/Qwen2.5-1.5B-Instruct`) | https://huggingface.co/jncraton/Qwen2.5-1.5B-Instruct-ct2-int8 |
| `gaudi/opus-mt-en-zh-ctranslate2` | CC-BY-4.0 (base: Helsinki-NLP/opus-mt-en-zh) | https://huggingface.co/gaudi/opus-mt-en-zh-ctranslate2 |

Models are downloaded separately by the user or shipped alongside the portable
build. They are not part of MeetSub's MIT-licensed source code. Review the model
cards before commercial use or redistribution. OPUS-MT's CC-BY-4.0 requires
attribution; retain the model source/license information if you redistribute it.
Qwen2.5 is Apache-2.0; M2M100 and Whisper weights are MIT.

## Fonts and Windows components

The overlay uses fonts installed on the user's Windows system (default
`Microsoft YaHei UI`); no font files are redistributed. The screen-share
exclusion feature uses the Windows `SetWindowDisplayAffinity` API.
