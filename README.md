# STORAGE HQs Web

Conversão da aplicação desktop em PyQt5 para uma aplicação web em Flask, pronta para publicação no **GitHub** e deploy no **Render**.

## O que a versão web entrega

- login com autenticação por sessão
- compatibilidade com o usuário legado `STAN_ADM`
- migração automática de hash SHA-256 legado para hash seguro do Werkzeug no primeiro login
- criação de até 2 administradores
- CRUD completo de coleções
- CRUD completo de HQs
- upload de capas para coleções e HQs
- biblioteca pública para visualização sem login
- persistência de banco SQLite e uploads em disco montado no Render

## Estrutura do projeto

- `webapp.py`: aplicação Flask
- `templates/`: páginas HTML
- `static/`: CSS e imagens base
- `render.yaml`: blueprint para deploy no Render
- `requirements.txt`: dependências Python

## Rodando localmente

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
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

Acesse em `http://127.0.0.1:5000`.

## Credenciais iniciais

Por padrão, a app cria o usuário admin abaixo na primeira execução:

- usuário: `STAN_ADM`
- senha: valor de `DEFAULT_ADMIN_PASSWORD`

No Render, configure essa variável no painel antes do primeiro deploy.

## Deploy no Render

1. Publique este projeto em um repositório GitHub.
2. No Render, escolha **Blueprint** ou **New Web Service** a partir do repositório.
3. Confirme o arquivo `render.yaml`.
4. Defina `DEFAULT_ADMIN_PASSWORD` com uma senha forte.
5. Faça o deploy.

## Observações importantes

- o Render precisa do disco montado em `/var/data` para preservar **SQLite** e **uploads** entre deploys;
- se você quiser migrar um banco antigo `storage_hqs.db`, copie esse arquivo para o diretório montado (`/var/data`) antes de iniciar a aplicação;
- como a estrutura do banco foi mantida compatível, dados antigos podem ser reaproveitados.

## Segurança

A senha padrão original do desktop era apenas para seed inicial. Troque imediatamente em produção criando novo admin e removendo o uso da senha padrão.
