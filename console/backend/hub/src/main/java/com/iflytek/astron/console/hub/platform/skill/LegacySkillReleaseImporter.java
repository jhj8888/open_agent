package com.iflytek.astron.console.hub.platform.skill;

import com.iflytek.astron.console.commons.tenant.*;
import java.io.IOException;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/** Explicit migration only. Old mutable drafts and their APIs remain unchanged. */
@Service
public class LegacySkillReleaseImporter {
    private final JdbcTemplate jdbc;
    private final TenantAccessService access;
    private final LegacySkillObjectReader reader;
    private final SkillReleaseService releases;
    public LegacySkillReleaseImporter(JdbcTemplate jdbc,TenantAccessService access,LegacySkillObjectReader reader,SkillReleaseService releases) {
        this.jdbc=jdbc;this.access=access;this.reader=reader;this.releases=releases;
    }
    public Map<String,Object> importDraft(String tenant,String environment,long id,String version,String image) {
        if(id<=0) throw new IllegalArgumentException("Invalid draft Skill ID");
        return releases.publishLegacy(tenant,environment,version,image,id,()->snapshot(tenant,environment,id));
    }
    private record Scope(Long space,String uid,String objectScope) {}
    private record Entry(long id,long parent,String name,String type,String key,long size,Object updated) {}
    private Scope scope(String tenant,String environment) {
        access.require(tenant,environment,TenantPermission.RESOURCE_WRITE);
        var rows=jdbc.queryForList("SELECT space_id,personal_uid FROM oa_tenant WHERE tenant_id=?",tenant);
        if(rows.isEmpty()) throw new TenantAccessDeniedException();
        var row=rows.getFirst();Number space=(Number)row.get("space_id");
        String uid=(String)row.get("personal_uid");
        if(space!=null) return new Scope(space.longValue(),null,"space-"+space.longValue());
        if(uid==null || uid.isBlank()) throw new TenantAccessDeniedException();
        return new Scope(null,uid,"user-"+uid.replaceAll("[^a-zA-Z0-9._-]","-").replaceAll("-{2,}","-"));
    }
    private List<Entry> query(Scope scope,String filter,long id) {
        // filter is selected by this class, never supplied by a request.
        String sql="SELECT id,parent_id,name,entry_type,object_key,file_size,update_time FROM skill_file WHERE deleted=0 AND ";
        List<Map<String,Object>> rows=scope.space()!=null
                ? jdbc.queryForList(sql+"space_id=? AND "+filter+"=? ORDER BY id LIMIT 201",scope.space(),id)
                : jdbc.queryForList(sql+"space_id IS NULL AND uid COLLATE utf8mb4_bin=? AND "+filter+"=? ORDER BY id LIMIT 201",scope.uid(),id);
        if(rows.size()>200) throw new IllegalArgumentException("Too many draft entries; split the Skill before import");
        return rows.stream().map(row->new Entry(((Number)row.get("id")).longValue(),((Number)row.get("parent_id")).longValue(),
                (String)row.get("name"),(String)row.get("entry_type"),(String)row.get("object_key"),((Number)row.get("file_size")).longValue(),row.get("update_time"))).toList();
    }
    private Map<String,Entry> tree(Scope scope,long id) {
        var rows=query(scope,"id",id);
        if(rows.size()!=1 || !"file".equals(rows.getFirst().type()) || !"SKILL.md".equals(rows.getFirst().name())) throw new TenantAccessDeniedException();
        var skill=rows.getFirst();Map<String,Entry> files=new TreeMap<>();files.put("SKILL.md",skill);
        if(skill.parent()==0) return files;
        var root=query(scope,"id",skill.parent());
        if(root.size()!=1 || !"folder".equals(root.getFirst().type())) throw new IllegalArgumentException("Skill parent folder is unavailable");
        record Folder(long id,String path) {}
        var queue=new ArrayDeque<Folder>();queue.add(new Folder(skill.parent(),""));
        Set<Long> visited=new HashSet<>();visited.add(skill.id());int entries=0;
        while(!queue.isEmpty()) {
            var folder=queue.removeFirst();
            if(!visited.add(folder.id())) throw new IllegalArgumentException("Cyclic draft folder tree");
            for(var entry:query(scope,"parent_id",folder.id())) {
                if(entry.id()==skill.id()) continue;
                if(++entries>200) throw new IllegalArgumentException("Too many draft entries");
                if(entry.name().contains("/") || entry.name().contains("\\")) throw new IllegalArgumentException("Invalid draft name");
                String path=SkillArchiveValidator.normalize(folder.path()+entry.name(),false);
                if("folder".equals(entry.type())) { queue.add(new Folder(entry.id(),path+"/"));continue; }
                if(!"file".equals(entry.type())) throw new IllegalArgumentException("Invalid draft entry type");
                // Match the existing import API: another Skill entry is not this Skill's resource.
                if(entry.name().equalsIgnoreCase("SKILL.md")) continue;
                if(!visited.add(entry.id()) || files.putIfAbsent(path,entry)!=null) throw new IllegalArgumentException("Duplicate draft path");
                if(files.size()>SkillArchiveValidator.MAX_FILES) throw new IllegalArgumentException("Too many Skill files");
            }
        }
        return files;
    }
    private SkillArchiveValidator.Validated snapshot(String tenant,String environment,long id) {
        Scope scope=scope(tenant,environment);var before=tree(scope,id);
        Map<String,byte[]> content=new TreeMap<>();long total=0;
        for(var file:before.entrySet()) {
            if(file.getValue().size()<0 || file.getValue().size()>SkillArchiveValidator.MAX_FILE_BYTES) throw new IllegalArgumentException("Draft file exceeds size limit");
            byte[] bytes=reader.read(scope.objectScope(),file.getValue().key());total+=bytes.length;
            if(total>SkillArchiveValidator.MAX_TOTAL_BYTES) throw new IllegalArgumentException("Skill exceeds total size limit");
            if(bytes.length!=file.getValue().size()) throw new SkillReleaseService.ReleaseConflictException("Draft changed during import; retry");
            content.put(file.getKey(),bytes);
        }
        access.require(tenant,environment,TenantPermission.RESOURCE_WRITE);
        if(!before.equals(tree(scope,id))) throw new SkillReleaseService.ReleaseConflictException("Draft changed during import; retry");
        try { return new SkillArchiveValidator().validateFiles(content); }
        catch(IOException exception) { throw new IllegalArgumentException("SKILL.md must be UTF-8"); }
    }
}
