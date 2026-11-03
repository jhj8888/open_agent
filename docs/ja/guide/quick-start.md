# クイックスタート

このページでは、初めて評価する方に向けた最短ルートをまとめています。まずはローカル環境で始め、必要に応じて完全なデプロイ・設定リファレンスへ進んでください。

## こんな方におすすめ

- まずローカルで全体の流れを試したい開発者
- ログイン可能なデモ環境をすばやく用意したいチーム
- 後から Docker Compose から Helm や本番設定へ移行する予定のユーザー

## 2 ステップで起動

### 1. リポジトリをクローンして環境変数を準備

```bash
git clone https://github.com/iflytek/astron-agent.git
cd astron-agent/docker/astronAgent
cp .env.example .env
```

コピー後、モデル・データベース・オブジェクトストレージ・認証などの設定を必要に応じて記入します。

### 2. サービスを起動

```bash
docker compose -f docker-compose-with-auth.yaml up -d
```

起動が完了すると、デフォルトのアクセス先は次のとおりです：

- Astron Agent フロントエンド：`http://localhost/`
- Casdoor 管理コンソール：`http://localhost:8000`

## 推奨の読み進め方

1. まず[デプロイ概要](/guide/deploy)を確認する
2. 続いて[設定の概要](/guide/config)に進む
3. 問題が発生した場合は [FAQ](/ja/faq) を参照する

## 詳細リファレンス

- [プロジェクト README](/ja/README)
- [認証付きデプロイガイド](/DEPLOYMENT_GUIDE_WITH_AUTH)
- [完全なデプロイガイド](/DEPLOYMENT_GUIDE)
