# Storage HQs - Biblioteca de Attilan

Aplicação web em Flask para curadoria de HQs, com biblioteca pública, painel administrativo, coleções, HQs e upload de capas.

## O que foi ajustado nesta versão

- identidade visual renovada da página principal
- título atualizado para **Storage HQs - Biblioteca de Attilan**
- home simplificada com foco em **Coleções** e **HQs**
- rodapé personalizado com seus créditos
- interface com visual mais moderno e mais geek
- suporte a persistência via `DATA_DIR` para SQLite e uploads

## Como a persistência funciona hoje

A aplicação salva dados em:

- banco SQLite: `DATA_DIR/storage_hqs.db`
- imagens: `DATA_DIR/uploads/...`

Em ambiente local, basta definir `DATA_DIR=./data`.

No Render, a aplicação **precisa** gravar em um local persistente. O filesystem padrão do serviço é efêmero, então dados locais desaparecem após restart ou novo deploy. Para produção, o ideal é uma destas opções:

1. **Persistent Disk do Render** montado em `/var/data`.
2. **Postgres** para os dados + armazenamento externo para imagens.
3. **Google Drive** apenas como integração planejada de backup/arquivos, não como banco principal.

## Google Drive: dá para usar?

Sim, **é possível integrar** a aplicação com Google Drive pela API do Google para:

- enviar capas para uma pasta no Drive
- baixar capas quando necessário
- manter backup do banco SQLite

Mas, para este projeto, **não é a melhor base de persistência principal**. O mais robusto é:

- usar **Persistent Disk** no Render para continuar com SQLite, ou
- migrar para **Postgres**.

O Google Drive faz mais sentido como:

- backup automático
- repositório de imagens
- exportação de acervo

## Execução local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY='troque-isto'
export DATA_DIR='./data'
python webapp.py
```

No Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SECRET_KEY='troque-isto'
$env:DATA_DIR='./data'
python webapp.py
```

## Credenciais iniciais

- usuário: `STAN_ADM`
- senha: valor configurado em `DEFAULT_ADMIN_PASSWORD`

## Deploy no Render

1. Publique no GitHub.
2. Crie o serviço web no Render.
3. Defina `SECRET_KEY`.
4. Defina `DEFAULT_ADMIN_PASSWORD`.
5. Garanta que `DATA_DIR` aponte para `/var/data`.
6. No plano pago, anexe um **Persistent Disk** em `/var/data`.

## Estrutura principal

- `webapp.py`: aplicação Flask
- `templates/`: páginas HTML
- `static/css/app.css`: tema visual
- `static/images/hero-pattern.svg`: fundo decorativo da home
- `render.yaml`: blueprint do serviço
