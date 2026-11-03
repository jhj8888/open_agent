# FAQ

このページは、ドキュメントサイトを初めて開いたときやデプロイ作業を始めたときに、多くのユーザーが遭遇するよくある質問に回答します。

## 現在の GitHub Pages サイトの仕組み

Pages サイトは、以前の `website/` ディレクトリを直接公開する方式ではなくなりました。現在は `docs/` 配下の VitePress ドキュメントサイトをビルドし、生成された静的ファイルを公開しています。

## なぜ VitePress に移行したのか

- 1 つの大きな HTML ファイルではなく、ディレクトリ単位でドキュメントを管理できる
- ホームページ、ガイド、設定、FAQ をそれぞれ独立して更新できる
- GitHub Pages と Vercel は静的ビルド成果物を公開するだけでよい
- ナビゲーションや検索、今後のセクション追加が容易

## GitHub Pages の設定変更は必要か

必要です。ワークフローがドキュメントをビルドして成果物をアップロードできるよう、リポジトリの Pages 設定で公開ソースを `GitHub Actions` に指定してください。

## ローカルでドキュメントをプレビューするには

`docs/` ディレクトリで以下を実行します：

```bash
npm install
npm run docs:dev
```

## より詳しいトラブルシューティングはどこで確認できますか

- [リポジトリ FAQ](https://github.com/iflytek/astron-agent/blob/main/FAQ.md)
- [GitHub Discussions](https://github.com/iflytek/astron-agent/discussions)
- [GitHub Issues](https://github.com/iflytek/astron-agent/issues)
