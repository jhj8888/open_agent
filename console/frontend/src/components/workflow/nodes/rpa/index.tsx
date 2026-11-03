import type { NodeComponentProps } from '@/components/workflow/nodes/types';
import { memo } from 'react';
import FixedOutputs from '../components/fixed-outputs';
import ExceptionHandling from '../components/exception-handling';
import SingleInput from '../components/single-input';

export const RpaDetail = memo((props: NodeComponentProps) => {
  const { id, data } = props;

  return (
    <div className="p-[14px] pb-[6px]">
      <SingleInput id={id} data={data} />
      <FixedOutputs id={id} data={data} />
      <ExceptionHandling id={id} data={data} />
    </div>
  );
});
