import React, { useEffect } from 'react';
import ReactMarkdown, { ExtraProps } from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { v4 as uuid } from 'uuid';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { github } from 'react-syntax-highlighter/dist/esm/styles/hljs';
import customFootnotePlugin from './custom-footnote-plugin';
import {
  markdownKatexOptions,
  markdownSanitizePlugin,
} from '../markdown-sanitize';
import 'katex/dist/katex.min.css';

function index({
  content = '',
  isSending = false,
}: {
  content: string;
  isSending: boolean;
}): React.ReactElement {
  const globalMarkdownId = uuid();

  function addCursorToLastElement(): void {
    // 清除之前的光标类
    const container = document.getElementById(globalMarkdownId);
    const mdContainer = container?.querySelector('.global-markdown');
    if (!mdContainer) {
      return;
    }
    const previousCursor = mdContainer.querySelector(
      '.global-markdown-flashing-cursor'
    );
    if (previousCursor) {
      previousCursor.classList.remove('global-markdown-flashing-cursor');
    }

    // 获取最后一个子元素
    const lastElement = getLastDeepestChild(mdContainer);

    if (lastElement) {
      lastElement.classList.add('global-markdown-flashing-cursor');
    }
  }

  function getLastDeepestChild(element: Element): Element {
    while (element?.lastElementChild) {
      element = element?.lastElementChild;
      if (element?.textContent?.trim()) {
        return element;
      }
    }
    return element;
  }

  function clearCursorToLastElement(): void {
    const container = document.getElementById(globalMarkdownId);
    const previousCursor = container?.querySelectorAll(
      '.global-markdown-flashing-cursor'
    );
    if (previousCursor) {
      Array.from(previousCursor).forEach(function (element) {
        element.classList.remove('global-markdown-flashing-cursor');
      });
    }
  }

  useEffect(() => {
    if (isSending) {
      addCursorToLastElement();
    } else {
      clearCursorToLastElement();
    }
  }, [content, isSending]);

  const MyLink = ({
    node,
    ...props
  }: React.AnchorHTMLAttributes<HTMLAnchorElement> &
    ExtraProps): React.ReactNode => (
    <a {...props} target="_blank" rel="noopener noreferrer">
      {props.children}
    </a>
  );

  const ImageRenderer = ({
    node,
    ...props
  }: React.ImgHTMLAttributes<HTMLImageElement> &
    ExtraProps): React.ReactNode => {
    return <img {...props} style={{ ...props.style, maxWidth: '100%' }} />;
  };

  return (
    <div
      id={globalMarkdownId}
      className="flex items-center justify-center markdown-body"
    >
      <ReactMarkdown
        skipHtml={false}
        className="global-markdown"
        remarkPlugins={[remarkMath, [remarkGfm, { singleTilde: false }]]}
        rehypePlugins={[
          rehypeRaw,
          markdownSanitizePlugin,
          [rehypeKatex, markdownKatexOptions],
          customFootnotePlugin,
        ]}
        components={{
          a: MyLink,
          img: ImageRenderer,
          code(props) {
            const { children, className, node, ref, ...rest } = props;

            const match = /language-(\w+)/.exec(className || '');
            return match && children ? (
              <SyntaxHighlighter
                {...rest}
                PreTag="div"
                children={String(children)}
                language={match[1]}
                style={github}
              />
            ) : (
              <code {...rest} ref={ref} className={className}>
                {children}
              </code>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default index;
