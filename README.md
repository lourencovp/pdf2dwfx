# pdf2dwfx

*[Português](#português) · [English](#english)*

---

## Português

**pdf2dwfx** converte desenhos vetoriais em PDF (exportados de software CAD) em ficheiros **DWFx**, prontos para submeter projetos de arquitetura às câmaras municipais portuguesas.

> ⚠️ **Em desenvolvimento.** Ainda não há uma versão utilizável.

### Objetivos

- Cada página do PDF torna-se uma folha DWFx com exatamente o mesmo tamanho de papel.
- As camadas do PDF tornam-se camadas DWFx que se podem ligar/desligar no Autodesk Design Review.
- Escala em metros, para que a ferramenta de medição do Design Review dê distâncias reais corretas.
- Tramas, preenchimentos e texto preservados.
- Ficheiros pequenos, dentro dos limites municipais (ex.: 500 KB por folha, 30 MB por ficheiro).

### Privacidade

A aplicação funciona **totalmente offline**: nunca se liga à internet, não envia telemetria nem relatórios automáticos. Os seus desenhos nunca saem do seu computador.

### Licença

[GNU AGPL-3.0](LICENSE).

---

## English

**pdf2dwfx** converts vector PDF drawings (exported from CAD software) into **DWFx** files, ready for submitting architecture projects to Portuguese municipalities (câmaras municipais).

> ⚠️ **Work in progress.** There is no usable release yet.

### Goals

- Each PDF page becomes one DWFx sheet with exactly the same paper size.
- PDF layers become DWFx layers that can be switched on/off in Autodesk Design Review.
- Scale in metres, so the Design Review measure tool reports correct real-world distances.
- Hatches, fills and text preserved.
- Small files that meet municipal limits (e.g. 500 KB per sheet, 30 MB per file).

### Privacy

The app works **fully offline**: it never connects to the internet and sends no telemetry or automatic reports. Your drawings never leave your computer.

### For developers

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync          # install dependencies
uv run pytest    # run the tests
```

### License

[GNU AGPL-3.0](LICENSE).
