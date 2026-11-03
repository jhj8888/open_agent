import React from 'react';

interface KnowledgeNameTextProps {
  name: string;
}

function KnowledgeNameText(props: KnowledgeNameTextProps): React.ReactElement {
  const { name } = props;
  return (
    <span
      className="text-second font-medium ml-1.5 text-overflow max-w-[500px]"
      title={name}
    >
      {name}
    </span>
  );
}

export default KnowledgeNameText;
