package com.iflytek.astron.console.hub.platform.run;

import com.iflytek.astron.console.commons.response.ApiResult;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/platform/tenants/{tenantId}/environments/{environment}/runs")
public class TaskHistoryController {
    private final TaskHistoryService history;
    public TaskHistoryController(TaskHistoryService history) { this.history=history; }
    @GetMapping
    public ResponseEntity<?> list(@PathVariable String tenantId, @PathVariable String environment,
            @RequestParam(defaultValue="50") int limit, @RequestParam(required=false) String before,
            @RequestParam(defaultValue="all") String state, @RequestParam(defaultValue="") String search) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(ApiResult.success(
                history.list(new RunLedger.Scope(tenantId,environment),limit,before,state,search)));
    }
}
