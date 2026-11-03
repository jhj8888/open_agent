package com.iflytek.astron.console.toolkit.entity.table.workflow;

import com.baomidou.mybatisplus.annotation.TableName;
import com.iflytek.astron.console.commons.util.WorkflowProtocolSanitizer;
import lombok.Data;

import java.util.Date;

@Data
@TableName("flow_protocol_temp")
public class FlowProtocolTemp {
    String flowId;
    Date createdTime;
    String bizProtocol;
    String sysProtocol;

    public void setBizProtocol(String bizProtocol) {
        this.bizProtocol = WorkflowProtocolSanitizer.sanitize(bizProtocol);
    }

    public void setSysProtocol(String sysProtocol) {
        this.sysProtocol = WorkflowProtocolSanitizer.sanitizeSystemProtocol(sysProtocol);
    }
}
