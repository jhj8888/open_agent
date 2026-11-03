package com.iflytek.astron.console.toolkit.mapper.workflow;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.iflytek.astron.console.toolkit.entity.table.workflow.WorkflowArtifact;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface WorkflowArtifactMapper extends BaseMapper<WorkflowArtifact> {

    @Select("""
            SELECT COUNT(*)
            FROM workflow_artifact
            WHERE workflow_id = #{workflowId}
              AND deleted = 0
            """)
    long countActiveByWorkflowId(@Param("workflowId") Long workflowId);

    @Select("""
            SELECT COALESCE(SUM(file_size), 0)
            FROM workflow_artifact
            WHERE workflow_id = #{workflowId}
              AND deleted = 0
            """)
    long sumActiveBytesByWorkflowId(@Param("workflowId") Long workflowId);
}
