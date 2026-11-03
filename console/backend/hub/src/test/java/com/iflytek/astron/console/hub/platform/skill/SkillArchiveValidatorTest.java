package com.iflytek.astron.console.hub.platform.skill;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import org.apache.commons.compress.archivers.zip.ZipArchiveEntry;
import org.apache.commons.compress.archivers.zip.ZipArchiveOutputStream;
import org.apache.commons.compress.archivers.zip.Zip64Mode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class SkillArchiveValidatorTest {
    @TempDir Path directory;
    private final SkillArchiveValidator validator=new SkillArchiveValidator();
    private static final byte[] SKILL="---\nname: sample-skill\ndescription: Synthetic skill for contract tests.\n---\nRead scripts/add.py.\n".getBytes(StandardCharsets.UTF_8);
    record Entry(String path,byte[] bytes,int mode) {
        Entry(String path,byte[] bytes) { this(path,bytes,0100644); }
    }
    Path archive(List<Entry> entries) throws Exception {
        Path path=Files.createTempFile(directory,"package-",".zip");
        try(var output=new ZipArchiveOutputStream(path)) {
            output.setUseZip64(Zip64Mode.Never);
            for(var item:entries) {
                var entry=new ZipArchiveEntry(item.path());
                entry.setUnixMode(item.mode());
                output.putArchiveEntry(entry);
                output.write(item.bytes());
                output.closeArchiveEntry();
            }
        }
        return path;
    }
    @Test void preservesContentAndProducesStableDigestsRegardlessOfArchiveOrder() throws Exception {
        var script=new Entry("scripts/add.py","print(2 + 3)".getBytes(StandardCharsets.UTF_8));
        var first=validator.validate(archive(List.of(new Entry("SKILL.md",SKILL),script)));
        var second=validator.validate(archive(List.of(script,new Entry("SKILL.md",SKILL))));
        assertEquals("sample-skill",first.name());
        assertEquals(first.files().stream().map(SkillArchiveValidator.FileContent::sha256).toList(),second.files().stream().map(SkillArchiveValidator.FileContent::sha256).toList());
        assertEquals(List.of("SKILL.md","scripts/add.py"),first.files().stream().map(SkillArchiveValidator.FileContent::path).toList());
        assertArrayEquals(SKILL,first.files().getFirst().bytes());
    }
    @Test void acceptsASingleMatchingSkillDirectory() throws Exception {
        var value=validator.validate(archive(List.of(new Entry("sample-skill/SKILL.md",SKILL),new Entry("sample-skill/data.txt",new byte[]{1,2}))));
        assertEquals(List.of("SKILL.md","data.txt"),value.files().stream().map(SkillArchiveValidator.FileContent::path).toList());
    }
    @ParameterizedTest @ValueSource(strings={"../escape.py","/etc/passwd","C:/boot.ini","scripts\\evil.py","scripts/../evil.py","scripts//evil.py","trailing./evil.py","a\n/b","e\u0301.txt"})
    void rejectsNonCanonicalPaths(String path) throws Exception {
        var zip=archive(List.of(new Entry("SKILL.md",SKILL),new Entry(path,new byte[]{1})));
        if(path.contains("\\")) {
            // The ZIP writer itself canonicalizes DOS names. Restore the raw bad name in both headers.
            byte[] bytes=Files.readAllBytes(zip);
            byte[] normalized=path.replace('\\','/').getBytes(StandardCharsets.UTF_8);
            byte[] original=path.getBytes(StandardCharsets.UTF_8);
            for(int i=0;i<=bytes.length-normalized.length;i++) {
                if(java.util.Arrays.equals(bytes,i,i+normalized.length,normalized,0,normalized.length))
                    System.arraycopy(original,0,bytes,i,original.length);
            }
            Files.write(zip,bytes);
        }
        assertThrows(IllegalArgumentException.class,()->validator.validate(zip));
        assertFalse(Files.exists(directory.resolve("escape.py")));
    }
    @Test void rejectsLinksDevicesCaseDuplicatesAndFileDirectoryCollisions() throws Exception {
        for(var extras:List.of(
                List.of(new Entry("link","/etc/passwd".getBytes(StandardCharsets.UTF_8),0120777)),
                List.of(new Entry("device",new byte[]{},0020600)),
                List.of(new Entry("x.txt",new byte[]{1}),new Entry("x.txt",new byte[]{2})),
                List.of(new Entry("x.txt",new byte[]{1}),new Entry("X.txt",new byte[]{2})),
                List.of(new Entry("data",new byte[]{1}),new Entry("data/item",new byte[]{2})))) {
            var entries=new ArrayList<Entry>(); entries.add(new Entry("SKILL.md",SKILL)); entries.addAll(extras);
            var zip=archive(entries);
            assertThrows(IllegalArgumentException.class,()->validator.validate(zip));
        }
    }
    @Test void rejectsCompressedBombAndExcessiveEntryCount() throws Exception {
        var bomb=archive(List.of(new Entry("SKILL.md",SKILL),new Entry("bomb.txt",new byte[1024*1024])));
        assertThrows(IllegalArgumentException.class,()->validator.validate(bomb));
        var entries=new ArrayList<Entry>(); entries.add(new Entry("SKILL.md",SKILL));
        for(int i=0;i<300;i++) entries.add(new Entry("f"+i,new byte[]{1}));
        var tooMany=archive(entries);
        assertThrows(IllegalArgumentException.class,()->validator.validate(tooMany));
    }
    @Test void checksActualTotalBytesWhileReadingEntries() throws Exception {
        byte[] random=new byte[16384];new Random(42).nextBytes(random);
        byte[] content=new byte[512*1024];
        for(int i=0;i<content.length;i++) content[i]=random[i%random.length];
        var entries=new ArrayList<Entry>(); entries.add(new Entry("SKILL.md",SKILL));
        for(int i=0;i<41;i++) entries.add(new Entry("f"+i,content));
        var zip=archive(entries);
        var error=assertThrows(IllegalArgumentException.class,()->validator.validate(zip));
        assertTrue(error.getMessage().contains("total size"));
    }
    @Test void rejectsUnsafeYamlAndMissingMetadata() throws Exception {
        for(String body:List.of("plain instructions", "---\nname: test\nname: duplicate\ndescription: no\n---\n",
                "---\nname: Test\ndescription: nope\n---\n", "---\nname: test\ndescription: !!java.net.URL [https://example.com]\n---\n")) {
            var zip=archive(List.of(new Entry("SKILL.md",body.getBytes(StandardCharsets.UTF_8))));
            assertThrows(IllegalArgumentException.class,()->validator.validate(zip));
        }
    }
    @Test void rejectsTruncatedArchivesBeforeParsingContent() throws Exception {
        Path zip=archive(List.of(new Entry("SKILL.md",SKILL)));
        byte[] bytes=Files.readAllBytes(zip);Files.write(zip,java.util.Arrays.copyOf(bytes,bytes.length-5));
        assertThrows(IllegalArgumentException.class,()->validator.validate(zip));
    }
}
