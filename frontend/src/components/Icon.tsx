import type { ReactNode, SVGProps } from "react";

export type IconName =
  | "chat"
  | "task"
  | "life"
  | "memory"
  | "folder"
  | "tool"
  | "settings"
  | "plus"
  | "upload"
  | "tune"
  | "send";

const paths: Record<IconName, ReactNode> = {
  chat: <><path d="M5 5h14v11H9l-4 3V5Z"/><path d="M8 9h8M8 12h5"/></>,
  task: <><path d="m4 6 1.5 1.5L8 5M11 6h9M4 12l1.5 1.5L8 11M11 12h9M4 18l1.5 1.5L8 17M11 18h9"/></>,
  life: <><path d="M12 3a7.5 7.5 0 1 0 7.5 7.5A6 6 0 0 1 12 3Z"/><path d="M17 4.5h.01M20 8h.01"/></>,
  memory: <><path d="M9 4.5a3 3 0 0 0-5 2.2 3.5 3.5 0 0 0 .7 6.8A3 3 0 0 0 9 17.8V4.5ZM15 4.5a3 3 0 0 1 5 2.2 3.5 3.5 0 0 1-.7 6.8A3 3 0 0 1 15 17.8V4.5Z"/><path d="M9 8H7.5M15 8h1.5M9 13H7M15 13h2"/></>,
  folder: <path d="M3.5 6.5A2.5 2.5 0 0 1 6 4h4l2 2h6A2.5 2.5 0 0 1 20.5 8.5v8A2.5 2.5 0 0 1 18 19H6a2.5 2.5 0 0 1-2.5-2.5v-10Z"/>,
  tool: <path d="M14.7 6.3a4 4 0 0 0-5-5L12 3.6 9.6 6 7.3 3.7a4 4 0 0 0 5 5L19 15.4a2.1 2.1 0 0 1-3 3l-6.7-6.7M6.5 13.5l-4 4a1.4 1.4 0 0 0 2 2l4-4"/>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3A1.7 1.7 0 0 0 14 21v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14h-.2v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
  upload: <path d="M12 16V4m0 0-4 4m4-4 4 4M5 14v5h14v-5"/>,
  tune: <><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></>,
  send: <path d="m5 12 14-7-4 14-3-5-7-2Zm7 2 3-3"/>,
};

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      {paths[name]}
    </svg>
  );
}
