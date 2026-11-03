import type { FlowType } from '@/components/workflow/types';
import { workflowBotId } from '../workflow-metadata';
import React, { useMemo, useState, useEffect } from 'react';
import FlowHeader from '../components/flow-header';
import { useParams } from 'react-router-dom';
import BotAnalysis from '@/components/config-page-component/bot-analysis';
import { getBotInfo } from '@/services/spark-common';
import { getFlowDetailAPI } from '@/services/flow';

function index(): React.ReactElement {
  const { id } = useParams();
  const [botInfo, setBotInfo] = useState<unknown>({});
  const [currentFlow, setCurrentFlow] = useState<FlowType>();
  const botId = useMemo(
    () => workflowBotId(currentFlow?.ext),
    [currentFlow?.ext]
  );

  useEffect(() => {
    id &&
      getFlowDetailAPI(id).then(data => {
        setCurrentFlow({
          ...data,
        });
      });
  }, [id]);
  useEffect(() => {
    if (botId) {
      getBotInfo({ botId }).then((data: unknown) => {
        setBotInfo(data);
      });
    }
  }, [botId]);

  return (
    <div>
      <FlowHeader currentFlow={currentFlow} />
      {botId && <BotAnalysis botId={botId} detailInfo={botInfo} />}
    </div>
  );
}

export default index;
