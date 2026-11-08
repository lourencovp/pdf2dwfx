# pdf2dwfx

*[Português](#português) · [English](#english)*

---

## Português

**pdf2dwfx** converte desenhos vetoriais em PDF (exportados de software CAD) em ficheiros **DWFx**, prontos para submeter projetos de arquitetura às câmaras municipais portuguesas.

> ⚠️ **Primeira versão (0.1).** Funciona, mas ainda está a ser melhorada. Verifique sempre o resultado no Autodesk Design Review antes de submeter.

### Descarregar e primeira utilização

Descarregue a versão mais recente na página [Releases](https://github.com/lourencovp/pdf2dwfx/releases/latest):

- **Windows:** `pdf2dwfx-windows.exe`. Não precisa de instalação: faça duplo clique. Na primeira vez o Windows pode mostrar "O Windows protegeu o seu PC"; clique em **Mais informações → Executar mesmo assim**.
- **macOS** (Macs com Apple silicon, M1 ou mais recente): `pdf2dwfx-macos.dmg`. Abra-o e arraste o pdf2dwfx para Aplicações. Na primeira vez, clique com o botão direito na aplicação, escolha **Abrir** e confirme (ou permita-a em Definições do Sistema → Privacidade e Segurança).
- **Linux:** `pdf2dwfx-linux.AppImage`. Torne-o executável (`chmod +x pdf2dwfx-linux.AppImage`) e faça duplo clique.

Estes avisos aparecem porque a aplicação ainda não tem assinatura digital (prevista para uma versão futura). Para confirmar que o ficheiro é o original, compare a sua impressão digital SHA-256 com `SHA256SUMS.txt` na mesma página.

**Como usar:** abra o PDF, verifique a escala de cada página (amarelo = sugerida, roxo = sem escala, verde = confirmada por si; ⚠️ = várias escalas na página), faça duplo clique numa página para definir viewports com escala própria, e clique em **Converter para DWFx**.

### Objetivos

- Cada página do PDF torna-se uma folha DWFx com exatamente o mesmo tamanho de papel.
- As camadas do PDF tornam-se camadas DWFx que se podem ligar/desligar no Autodesk Design Review.
- Escala em metros, para que a ferramenta de medição do Design Review dê distâncias reais corretas.
- Tramas, preenchimentos e texto preservados, com coordenadas tão precisas como no PDF.

### Privacidade

A aplicação funciona **totalmente offline**: nunca se liga à internet, não envia telemetria nem relatórios automáticos. Os seus desenhos nunca saem do seu computador. Se a aplicação falhar, guarda um relatório no seu computador (sem conteúdo dos desenhos nem nomes de ficheiros) e é você quem decide se o partilha no GitHub.

### Licença

[GNU AGPL-3.0](LICENSE).

---

## English

**pdf2dwfx** converts vector PDF drawings (exported from CAD software) into **DWFx** files, ready for submitting architecture projects to Portuguese municipalities (câmaras municipais).

> ⚠️ **First version (0.1).** It works, but is still being improved. Always check the result in Autodesk Design Review before submitting.

### Download and first start

Download the latest version from the [Releases](https://github.com/lourencovp/pdf2dwfx/releases/latest) page:

- **Windows:** `pdf2dwfx-windows.exe`. Nothing to install: double-click it. The first time, Windows may show "Windows protected your PC"; click **More info → Run anyway**.
- **macOS** (Apple-silicon Macs, M1 or newer): `pdf2dwfx-macos.dmg`. Open it and drag pdf2dwfx to Applications. The first time, right-click the app, choose **Open** and confirm (or allow it in System Settings → Privacy & Security).
- **Linux:** `pdf2dwfx-linux.AppImage`. Make it executable (`chmod +x pdf2dwfx-linux.AppImage`) and double-click it.

These warnings appear because the app is not digitally signed yet (planned for a later version). To check that a file is the original, compare its SHA-256 fingerprint with `SHA256SUMS.txt` on the same page.

**How to use:** open the PDF, check each page's scale (yellow = suggested, purple = no scale, green = checked by you; ⚠️ = several scales on the page), double-click a page to set viewports with their own scale, and click **Convert to DWFx**.

### Goals

- Each PDF page becomes one DWFx sheet with exactly the same paper size.
- PDF layers become DWFx layers that can be switched on/off in Autodesk Design Review.
- Scale in metres, so the Design Review measure tool reports correct real-world distances.
- Hatches, fills and text preserved, with coordinates as precise as in the PDF.

### Privacy

The app works **fully offline**: it never connects to the internet and sends no telemetry or automatic reports. Your drawings never leave your computer. If the app fails, it saves a report on your computer (no drawing content or file names) and you decide whether to share it on GitHub.

### For developers

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync          # install dependencies
uv run pytest    # run the tests
uv run pdf2dwfx-app [drawing.pdf]   # the app window: check scales, convert
uv run python packaging/build.py    # build the installable app for this system (dist/release/)

uv run pdf2dwfx drawing.pdf              # writes drawing.dwfx next to the PDF
uv run pdf2dwfx drawing.pdf --pages 1-3,7 -o sheets.dwfx
```

**Scales.** Each sheet measures in metres in Design Review. The scale of
each page is found automatically (from PDF measurement data, or from scale
texts such as "ESCALA: 1:100" and drawing titles such as "CORTE AA' 1:5",
which get their own viewport). Check and correct them:

```sh
uv run pdf2dwfx drawing.pdf --show-scales                  # list what was found, do not convert
uv run pdf2dwfx drawing.pdf --scale "1-4=1:200,9=paper"    # whole-sheet scales
uv run pdf2dwfx drawing.pdf --viewport "17:20,150,145,285=1:5"  # area in mm from the top-left corner
uv run pdf2dwfx drawing.pdf --no-suggest                   # no guessing: paper millimetres
```

### License

[GNU AGPL-3.0](LICENSE).
