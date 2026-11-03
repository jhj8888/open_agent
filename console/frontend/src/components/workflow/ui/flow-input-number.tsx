import React, { memo } from 'react';
import { InputNumber, type InputNumberProps } from 'antd';
import { cn } from '@/utils';

function FlowInputNumber<T extends string | number = number>({
  className = '',
  ...reset
}: InputNumberProps<T>): React.ReactElement {
  return (
    <div onKeyDown={e => e.stopPropagation()}>
      <InputNumber<T>
        controls={false}
        placeholder="请输入"
        className={cn('flow-input-number', className)}
        {...reset}
      />
    </div>
  );
}

export default memo(FlowInputNumber) as typeof FlowInputNumber;
