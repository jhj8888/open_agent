package com.iflytek.astron.console.hub.platform.skill;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.iflytek.astron.console.commons.tenant.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.function.Supplier;
import org.junit.jupiter.api.*;
import org.springframework.jdbc.core.JdbcTemplate;

class LegacySkillReleaseImporterTest {
    private final JdbcTemplate jdbc=mock(JdbcTemplate.class);
    private final TenantAccessService access=mock(TenantAccessService.class);
    private final LegacySkillObjectReader reader=mock(LegacySkillObjectReader.class);
    private final SkillReleaseService releases=mock(SkillReleaseService.class);
    private final LegacySkillReleaseImporter importer=new LegacySkillReleaseImporter(jdbc,access,reader,releases);
    private final byte[] skill="---\nname: sample\ndescription: Imported skill\n---\nRun scripts/add.py".getBytes(StandardCharsets.UTF_8);
    private final String key="skill-files/space-10/"+"a".repeat(32)+"/SKILL.md";
    private SkillArchiveValidator.Validated captured;
    private Map<String,Object> entry(long id,long parent,String name,String type,String objectKey,long size) {
        var row=new HashMap<String,Object>();row.put("id",id);row.put("parent_id",parent);row.put("name",name);row.put("entry_type",type);
        row.put("object_key",objectKey);row.put("file_size",size);row.put("update_time","2026-11-04T07:00:00");return row;
    }
    @BeforeEach void setup() {
        when(releases.publishLegacy(anyString(),anyString(),anyString(),anyString(),anyLong(),any())).thenAnswer(call->{
            captured=call.<Supplier<SkillArchiveValidator.Validated>>getArgument(5).get();return Map.of("releaseId","fixed-release");
        });
        when(jdbc.queryForList(anyString(),eq("space-10"))).thenReturn(List.of(Map.of("space_id",10L)));
        when(jdbc.queryForList(anyString(),eq(10L),eq(7L))).thenReturn(List.of(entry(7,0,"SKILL.md","file",key,skill.length)));
        when(reader.read("space-10",key)).thenReturn(skill.clone());
    }
    @Test void importsTheAuthorizedDraftWithoutChangingItsRowsOrBytes() {
        importer.importDraft("space-10","test",7,"legacy-7-v1","image");
        assertEquals("sample",captured.name());assertArrayEquals(skill,captured.files().getFirst().bytes());
        verify(reader).read("space-10",key);
        verify(jdbc,never()).update(anyString(),any(Object[].class));
    }
    @Test void aDraftIdFromAnotherTenantCannotReachObjectStorage() {
        when(jdbc.queryForList(anyString(),eq("space-20"))).thenReturn(List.of(Map.of("space_id",20L)));
        assertThrows(TenantAccessDeniedException.class,()->importer.importDraft("space-20","test",7,"v1","image"));
        verifyNoInteractions(reader);
    }
    @Test void importsScriptsFromOnlyTheSelectedParentFolder() {
        var root=entry(8,0,"sample","folder",null,0);
        var main=entry(7,8,"SKILL.md","file",key,skill.length);
        var directory=entry(9,8,"scripts","folder",null,0);
        var script=entry(10,9,"add.py","file",key.replace("SKILL.md","add.py"),1);
        when(jdbc.queryForList(anyString(),eq(10L),anyLong())).thenAnswer(call->{
            String sql=call.getArgument(0);long id=call.getArgument(2);
            if(sql.contains("parent_id=?")) return id==8?List.of(main,directory):id==9?List.of(script):List.of();
            return id==7?List.of(main):id==8?List.of(root):List.of();
        });
        when(reader.read("space-10",(String)script.get("object_key"))).thenReturn(new byte[]{42});
        importer.importDraft("space-10","test",7,"v1","image");
        assertEquals(List.of("SKILL.md","scripts/add.py"),captured.files().stream().map(SkillArchiveValidator.FileContent::path).toList());
        assertEquals(skill.length+1,captured.totalBytes());
    }
    @Test void observedDraftChangesAbortBeforePublishing() {
        var changed=entry(7,0,"SKILL.md","file",key,skill.length);changed.put("update_time","later");
        when(jdbc.queryForList(anyString(),eq(10L),eq(7L)))
                .thenReturn(List.of(entry(7,0,"SKILL.md","file",key,skill.length))).thenReturn(List.of(changed));
        assertThrows(SkillReleaseService.ReleaseConflictException.class,()->importer.importDraft("space-10","test",7,"v1","image"));
        assertNull(captured);
    }
    @Test void missingProductionWritePermissionStopsBeforeDraftReads() {
        doThrow(new TenantAccessDeniedException()).when(access).require("space-10","prod",TenantPermission.RESOURCE_WRITE);
        assertThrows(TenantAccessDeniedException.class,()->importer.importDraft("space-10","prod",7,"v1","image"));
        verifyNoInteractions(jdbc,reader);
    }
    @Test void directFileValidationRejectsUnsafeLegacyMetadataAndPaths() throws Exception {
        var validator=new SkillArchiveValidator();
        assertThrows(IllegalArgumentException.class,()->validator.validateFiles(Map.of("SKILL.md",skill,"../escape",new byte[]{1})));
        assertThrows(IllegalArgumentException.class,()->validator.validateFiles(Map.of("SKILL.md",skill,"a",new byte[]{1},"A",new byte[]{2})));
        assertThrows(IllegalArgumentException.class,()->validator.validateFiles(Map.of("SKILL.md","plain instructions".getBytes(StandardCharsets.UTF_8))));
        assertArrayEquals(skill,validator.validateFiles(Map.of("SKILL.md",skill)).files().getFirst().bytes());
    }
}
