import React, { memo } from 'react';
import { Tree, type TreeProps } from 'antd';
import type { BasicDataNode, DataNode } from 'rc-tree/lib/interface';

import flowArrowDown from '@/assets/imgs/workflow/flow-arrow-down.png';

function FLowTree<T extends BasicDataNode = DataNode>({
  treeData = [],
  showLine = true,
  ...reset
}: TreeProps<T>): React.ReactElement {
  return (
    <Tree<T>
      showLine={showLine}
      switcherIcon={({ expanded }: { expanded?: boolean }) => (
        <img
          src={flowArrowDown}
          className="w-[8px] h-[7px]"
          style={{
            transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)',
            transition: 'transform 0.3s ease',
          }}
        />
      )}
      defaultExpandAll
      treeData={treeData}
      {...reset}
    />
  );
}

export default memo(FLowTree) as typeof FLowTree;
