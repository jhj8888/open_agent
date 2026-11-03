import React from 'react';

// 类型定义
export interface KnowledgeDetailProps {
  setCurrentTab: (tab: string) => void;
  parentId: number | string;
  setParentId: (id: number | string) => void;
  setFileId: (id: number | string) => void;
}

export interface EditChunkProps {
  setEditModal: (show: boolean) => void;
  currentChunk: ChunkItem;
  enableChunk: (chunk: ChunkItem, checked: boolean) => void;
  fileInfo: FileInfo;
}

export interface FileDetailProps {
  setCurrentTab: (tab: string) => void;
  fileId: number | string;
  setFileId: (id: number | string) => void;
}

export type KnowledgeFileItem = import('@/types/resource').FileItem;

export type ChunkItem = import('@/types/resource').Chunk;

export interface TagItem {
  type: number;
  tagName: string;
}

export interface FileInfo {
  name: string;
  type: string;
}

export type DirectoryItem =
  import('@/types/resource').FileDirectoryTreeResponse;

export interface PaginationState {
  current: number;
  pageSize: number;
  total: number;
}

export interface KnowledgeDetailModalInfo {
  open: boolean;
  nodeId: string;
  repoId: string;
  tag?: string;
}

export interface useKnowledgeDetailProps {
  getDirectoryTree: () => void;
  getFiles: () => void;
  repoId: string;
  tag: string;
  isPro: boolean;
  id: string;
  handleInputChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  checkedIds: string[];
  ragType: string;
}

export interface useFileDetailProps {
  getFiles: () => void;
  getFileInfo: () => void;
  fetchData: (value?: string) => void;
  enableChunk: (chunk: ChunkItem, checked: boolean) => void;
  fetchDataDebounce: (e: React.ChangeEvent<HTMLInputElement>) => void;
  handleScroll: () => void;
  otherFiles: KnowledgeFileItem[];
}
