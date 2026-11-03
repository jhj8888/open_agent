package com.iflytek.astron.console.toolkit.mapper.workflow;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import java.util.List;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface WorkflowMapper extends BaseMapper<Workflow> {

    List<Workflow> selectSuqareFlowList(@Param("page") Page<Workflow> page,
            @Param("uid") String uid,
            @Param("configId") Integer configId,
            @Param("adminUid") String adminUid,
            @Param("name") String name);

    Integer checkDomainIsUsage(@Param("uid") String uid, @Param("domain") String domain);

    /**
     * Serializes artifact quota checks for one workflow. Every artifact upload locks this durable row
     * before reading usage and keeps the lock until its artifact record is committed.
     */
    @Select("SELECT * FROM workflow WHERE id = #{workflowId} AND deleted = 0 FOR UPDATE")
    Workflow selectActiveByIdForArtifactQuotaLock(@Param("workflowId") Long workflowId);
}
