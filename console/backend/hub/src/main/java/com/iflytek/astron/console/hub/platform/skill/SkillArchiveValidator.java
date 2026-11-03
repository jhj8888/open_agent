package com.iflytek.astron.console.hub.platform.skill;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.util.*;
import java.util.zip.CRC32;
import org.apache.commons.compress.archivers.zip.ZipArchiveEntry;
import org.apache.commons.compress.archivers.zip.ZipFile;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

/** Parses an opaque bounded ZIP; never extracts or executes a file on the host. */
public final class SkillArchiveValidator {
    public static final int MAX_ARCHIVE_BYTES=8*1024*1024;
    public static final int MAX_FILE_BYTES=5*1024*1024;
    public static final int MAX_TOTAL_BYTES=20*1024*1024;
    public static final int MAX_FILES=100;
    public static final int MAX_ENTRIES=200;

    public record FileContent(String path, byte[] bytes, String sha256) {}
    public record Validated(String name, String description, List<FileContent> files, long totalBytes) {}

    public Validated validate(Path archive) throws IOException {
        preflight(archive);
        try(var zip=ZipFile.builder().setPath(archive).setMaxNumberOfDisks(1).get()) {
            List<ZipArchiveEntry> entries=Collections.list(zip.getEntries());
            if(entries.isEmpty() || entries.size()>MAX_ENTRIES) throw invalid("Too many archive entries");
            Set<String> paths=new HashSet<>();
            Map<String,ZipArchiveEntry> files=new TreeMap<>();
            for(var entry:entries) {
                // Commons normalizes DOS backslashes in getName(); inspect the original bytes too.
                for(byte value:entry.getRawName()) if(value=='\\') throw invalid("Invalid archive path");
                int mode=entry.getUnixMode() & 0170000;
                if(entry.isUnixSymlink() || (mode!=0 && mode!=0100000 && mode!=0040000)
                        || (mode==0040000 && !entry.isDirectory()) || entry.getGeneralPurposeBit().usesEncryption()
                        || !zip.canReadEntryData(entry) || (entry.getMethod()!=0 && entry.getMethod()!=8)) {
                    throw invalid("Links, devices, encrypted or unsupported entries are forbidden");
                }
                String path=normalize(entry.getName(),entry.isDirectory());
                if(entry.isDirectory() && entry.getSize()!=0) throw invalid("Directory entries cannot contain data");
                if(!paths.add(path.toLowerCase(Locale.ROOT))) throw invalid("Duplicate or case-colliding archive path");
                if(!entry.isDirectory()) files.put(path,entry);
            }
            if(files.isEmpty() || files.size()>MAX_FILES) throw invalid("Too many Skill files");
            for(String path:files.keySet()) {
                for(String other:paths) {
                    if(other.startsWith(path.toLowerCase(Locale.ROOT)+"/")) throw invalid("File/directory path collision");
                }
            }
            String prefix="";
            if(!files.containsKey("SKILL.md")) {
                var skills=files.keySet().stream().filter(path->path.endsWith("/SKILL.md")).toList();
                if(skills.size()!=1 || skills.getFirst().split("/").length!=2) throw invalid("ZIP must contain one root SKILL.md or one Skill directory");
                prefix=skills.getFirst().substring(0,skills.getFirst().length()-"SKILL.md".length());
                final String root=prefix;
                if(files.keySet().stream().anyMatch(path->!path.startsWith(root))) throw invalid("Files outside the Skill directory are forbidden");
            }
            List<FileContent> result=new ArrayList<>();
            long total=0;
            byte[] skill=null;
            for(var item:files.entrySet()) {
                var entry=item.getValue();
                if(entry.getSize()<0 || entry.getSize()>MAX_FILE_BYTES || entry.getCompressedSize()<0
                        || (entry.getSize()>1024 && entry.getSize()>Math.max(1,entry.getCompressedSize())*100)) {
                    throw invalid("Skill entry exceeds size or compression ratio limits");
                }
                byte[] bytes;
                try(InputStream input=zip.getInputStream(entry)) { bytes=readBounded(input,Math.min(MAX_FILE_BYTES,MAX_TOTAL_BYTES-(int)total)); }
                CRC32 crc=new CRC32(); crc.update(bytes);
                if(bytes.length!=entry.getSize() || crc.getValue()!=entry.getCrc()) throw invalid("Archive size or CRC does not match");
                total+=bytes.length;
                String path=item.getKey().substring(prefix.length());
                if(path.equals("SKILL.md")) skill=bytes;
                result.add(new FileContent(path,bytes,sha256(bytes)));
            }
            if(skill==null || skill.length>512*1024) throw invalid("SKILL.md is missing or too large");
            var metadata=metadata(skill);
            if(!prefix.isEmpty() && !prefix.equals(metadata.get("name")+"/")) throw invalid("Skill directory must match its name");
            return new Validated(metadata.get("name"),metadata.get("description"),List.copyOf(result),total);
        }
    }

    public static String normalize(String path, boolean directory) {
        if(path==null || path.isBlank() || path.length()>1024 || path.startsWith("/") || path.contains("\\") || path.contains(":")
                || path.codePoints().anyMatch(c->Character.isISOControl(c)) || !Normalizer.isNormalized(path,Normalizer.Form.NFC)) {
            throw invalid("Invalid archive path");
        }
        String value=directory && path.endsWith("/") ? path.substring(0,path.length()-1) : path;
        for(String part:value.split("/",-1)) {
            if(part.isBlank() || part.equals(".") || part.equals("..") || !part.equals(part.strip()) || part.endsWith(".")) throw invalid("Invalid archive path");
        }
        return value;
    }

    /** Existing object-store drafts are plain file bytes, never filesystem entries. */
    public Validated validateFiles(Map<String,byte[]> contents) throws IOException {
        if(contents.isEmpty() || contents.size()>MAX_FILES) throw invalid("Too many Skill files");
        var files=new ArrayList<FileContent>();
        var paths=new HashSet<String>();
        long total=0;
        for(var entry:new TreeMap<>(contents).entrySet()) {
            String path=normalize(entry.getKey(),false);
            if(!paths.add(path.toLowerCase(Locale.ROOT))) throw invalid("Duplicate or case-colliding archive path");
            byte[] bytes=entry.getValue();total+=bytes.length;
            if(bytes.length>MAX_FILE_BYTES || total>MAX_TOTAL_BYTES) throw invalid("Skill exceeds file or total size limit");
            files.add(new FileContent(path,bytes,sha256(bytes)));
        }
        for(String path:paths) for(String other:paths)
            if(other.startsWith(path+"/")) throw invalid("File/directory path collision");
        byte[] skill=contents.get("SKILL.md");
        if(skill==null || skill.length>512*1024) throw invalid("SKILL.md is missing or too large");
        var metadata=metadata(skill);
        return new Validated(metadata.get("name"),metadata.get("description"),List.copyOf(files),total);
    }

    public static byte[] readBounded(InputStream input, int limit) throws IOException {
        if(limit<0) throw invalid("Skill exceeds total size limit");
        var output=new ByteArrayOutputStream(Math.min(limit,8192));
        byte[] buffer=new byte[8192];
        int count;
        while((count=input.read(buffer))!=-1) {
            if(count==0) continue;
            if(count>limit-output.size()) throw invalid("Skill exceeds file or total size limit");
            output.write(buffer,0,count);
        }
        return output.toByteArray();
    }

    private static Map<String,String> metadata(byte[] bytes) throws IOException {
        String text=StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString().replace("\r\n","\n");
        if(!text.startsWith("---\n")) throw invalid("SKILL.md requires YAML name and description");
        int end=text.indexOf("\n---",4);
        if(end<0 || end>32768 || (end+4<text.length() && text.charAt(end+4)!='\n')) throw invalid("Invalid SKILL.md frontmatter");
        var options=new LoaderOptions();
        options.setAllowDuplicateKeys(false);
        options.setMaxAliasesForCollections(0);
        options.setNestingDepthLimit(10);
        options.setCodePointLimit(32768);
        Object value;
        try { value=new Yaml(new SafeConstructor(options)).load(text.substring(4,end)); }
        catch(RuntimeException exception) { throw invalid("Invalid SKILL.md frontmatter"); }
        if(!(value instanceof Map<?,?> map) || !(map.get("name") instanceof String name) || !(map.get("description") instanceof String description))
            throw invalid("SKILL.md requires text name and description");
        if(name.isEmpty() || !name.equals(name.toLowerCase(Locale.ROOT)) || name.codePointCount(0,name.length())>64 || name.startsWith("-") || name.endsWith("-") || name.contains("--")
                || !Normalizer.isNormalized(name,Normalizer.Form.NFC)
                || !name.codePoints().allMatch(c->c=='-' || Character.isDigit(c) || (Character.isLetter(c) && !Character.isUpperCase(c))))
            throw invalid("Invalid Skill name");
        if(description.isBlank() || description.codePointCount(0,description.length())>1024) throw invalid("Invalid Skill description");
        return Map.of("name",name,"description",description);
    }

    /** Commons ZipFile has no pre-allocation entry cap. Inspect only the fixed
     * classic ZIP footer before invoking its central-directory parser. ZIP64 and
     * split archives are unnecessary at our limits and are deliberately rejected. */
    private static void preflight(Path path) throws IOException {
        long size=Files.size(path);
        if(size<22 || size>MAX_ARCHIVE_BYTES) throw invalid("ZIP size must be between 22 bytes and 8 MiB");
        int length=(int)Math.min(size,65557);
        ByteBuffer tail=ByteBuffer.allocate(length).order(ByteOrder.LITTLE_ENDIAN);
        try(var channel=Files.newByteChannel(path)) {
            channel.position(size-length);
            while(tail.hasRemaining()) if(channel.read(tail)<0) throw invalid("Truncated ZIP archive");
        }
        for(int i=length-22;i>=0;i--) {
            if(tail.getInt(i)!=0x06054b50 || i+22+Short.toUnsignedInt(tail.getShort(i+20))!=length) continue;
            int count=Short.toUnsignedInt(tail.getShort(i+10));
            long centralSize=Integer.toUnsignedLong(tail.getInt(i+12));
            long offset=Integer.toUnsignedLong(tail.getInt(i+16));
            if(tail.getShort(i+4)!=0 || tail.getShort(i+6)!=0 || Short.toUnsignedInt(tail.getShort(i+8))!=count
                    || count<1 || count>MAX_ENTRIES || centralSize>256*1024 || offset+centralSize!=size-length+i)
                throw invalid("Unsupported ZIP layout or too many entries");
            return;
        }
        throw invalid("Invalid ZIP footer");
    }

    public static String sha256(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch(java.security.NoSuchAlgorithmException exception) { throw new IllegalStateException("SHA-256 is unavailable"); }
    }
    private static IllegalArgumentException invalid(String message) { return new IllegalArgumentException(message); }
}
