package com.iflytek.astron.console.toolkit.service.workflow;

import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.toolkit.config.properties.SkillSandboxArtifactProperties;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.zip.ZipFile;
import org.apache.commons.compress.archivers.zip.ZipArchiveEntry;
import org.apache.commons.io.FilenameUtils;
import org.apache.commons.lang3.StringUtils;
import org.apache.poi.openxml4j.opc.OPCPackage;
import org.apache.poi.openxml4j.opc.PackageAccess;
import org.apache.poi.openxml4j.opc.PackagePart;
import org.apache.poi.openxml4j.opc.PackageRelationship;
import org.apache.poi.openxml4j.opc.PackageRelationshipTypes;
import org.apache.poi.openxml4j.opc.TargetMode;
import org.apache.poi.openxml4j.util.ZipSecureFile;
import org.apache.poi.poifs.filesystem.DirectoryEntry;
import org.apache.poi.poifs.filesystem.Entry;
import org.apache.poi.poifs.filesystem.POIFSFileSystem;
import org.apache.tika.Tika;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

/** Validates workflow artifacts using both filename policy and server-side content detection. */
@Component
public class WorkflowArtifactFileValidator {

    private static final int MAX_FILE_NAME_LENGTH = 255;
    private static final int COPY_BUFFER_SIZE = 8192;
    private static final int MAX_OOXML_ENTRY_COUNT = 512;
    private static final long MAX_OOXML_ENTRY_BYTES = 32L * 1024 * 1024;
    private static final long MAX_OOXML_XML_ENTRY_BYTES = 8L * 1024 * 1024;
    private static final long MAX_OOXML_CONTROL_XML_ENTRY_BYTES = 256L * 1024;
    private static final long MAX_OOXML_TOTAL_EXPANDED_BYTES = 64L * 1024 * 1024;
    private static final int MAX_OLE_ENTRY_COUNT = 512;
    private static final int MAX_OLE_DIRECTORY_DEPTH = 32;
    private static final String OCTET_STREAM = "application/octet-stream";
    private static final Set<String> ACTIVE_CONTENT_TYPES = Set.of(
            "application/javascript",
            "application/xhtml+xml",
            "image/svg+xml",
            "text/html",
            "text/javascript");
    private static final Map<String, Set<String>> MEDIA_TYPES_BY_EXTENSION = Map.ofEntries(
            Map.entry("txt", Set.of("text/plain")),
            Map.entry("md", Set.of("text/markdown", "text/plain")),
            Map.entry("csv", Set.of("text/csv", "text/plain")),
            Map.entry("json", Set.of("application/json", "text/plain")),
            Map.entry("pdf", Set.of("application/pdf")),
            Map.entry("png", Set.of("image/png")),
            Map.entry("jpg", Set.of("image/jpeg")),
            Map.entry("jpeg", Set.of("image/jpeg")),
            Map.entry("gif", Set.of("image/gif")),
            Map.entry("webp", Set.of("image/webp")),
            Map.entry("doc", Set.of("application/msword", "application/x-tika-msoffice")),
            Map.entry("xls", Set.of("application/vnd.ms-excel", "application/x-tika-msoffice")),
            Map.entry("ppt", Set.of("application/vnd.ms-powerpoint", "application/x-tika-msoffice")),
            Map.entry(
                    "docx",
                    Set.of(
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            "application/x-tika-ooxml",
                            "application/zip")),
            Map.entry(
                    "xlsx",
                    Set.of(
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            "application/x-tika-ooxml",
                            "application/zip")),
            Map.entry(
                    "pptx",
                    Set.of(
                            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                            "application/x-tika-ooxml",
                            "application/zip")),
            Map.entry("zip", Set.of("application/zip", "application/x-zip-compressed")));
    private static final Map<String, String> OOXML_MAIN_CONTENT_TYPE_BY_EXTENSION = Map.of(
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml");
    private static final Map<String, Set<String>> OLE_ROOT_ENTRIES_BY_EXTENSION = Map.of(
            "doc", Set.of("WordDocument"),
            "xls", Set.of("Workbook", "Book"),
            "ppt", Set.of("PowerPoint Document"));
    private static final Set<String> FORBIDDEN_OLE_ENTRY_NAMES = Set.of(
            "_vba_project_cur", "vba", "macros", "objectpool");

    private final SkillSandboxArtifactProperties properties;
    private final OoxmlResourceLimits ooxmlResourceLimits;
    private final Tika tika = new Tika();

    @Autowired
    public WorkflowArtifactFileValidator(SkillSandboxArtifactProperties properties) {
        this(
                properties,
                new OoxmlResourceLimits(
                        MAX_OOXML_ENTRY_COUNT,
                        MAX_OOXML_ENTRY_BYTES,
                        MAX_OOXML_XML_ENTRY_BYTES,
                        MAX_OOXML_CONTROL_XML_ENTRY_BYTES,
                        MAX_OOXML_TOTAL_EXPANDED_BYTES));
    }

    WorkflowArtifactFileValidator(
            SkillSandboxArtifactProperties properties, OoxmlResourceLimits ooxmlResourceLimits) {
        this.properties = Objects.requireNonNull(properties);
        this.ooxmlResourceLimits = Objects.requireNonNull(ooxmlResourceLimits);
    }

    public ValidatedArtifact validate(MultipartFile file) {
        if (file == null || file.isEmpty() || StringUtils.isBlank(file.getOriginalFilename())) {
            throw new BusinessException(ResponseEnum.PARAM_ERROR);
        }
        if (file.getSize() <= 0 || file.getSize() > properties.getArtifactMaxFileSize().toBytes()) {
            throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_FILE_TOO_LARGE);
        }

        String fileName = normalizeFileName(file.getOriginalFilename());
        String extension = StringUtils.lowerCase(FilenameUtils.getExtension(fileName), Locale.ROOT);
        Set<String> configuredExtensions = properties.getArtifactAllowedExtensions();
        if (StringUtils.isBlank(extension)
                || configuredExtensions.stream().noneMatch(extension::equalsIgnoreCase)
                || !MEDIA_TYPES_BY_EXTENSION.containsKey(extension)) {
            throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_FILE_TYPE_NOT_ALLOWED);
        }

        String declaredType = normalizeMediaType(file.getContentType());
        if (ACTIVE_CONTENT_TYPES.contains(declaredType)
                || (!StringUtils.isBlank(declaredType)
                        && !OCTET_STREAM.equals(declaredType)
                        && !MEDIA_TYPES_BY_EXTENSION.get(extension).contains(declaredType))) {
            throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
        }

        // Preflight Office containers before the general detector. OOXML resource limits run
        // before its POI package parse inside validateOoxmlContainer.
        validateOfficeContainer(file, extension);
        String detectedType;
        try (InputStream input = file.getInputStream()) {
            detectedType = normalizeMediaType(tika.detect(input, fileName));
        } catch (IOException exception) {
            throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
        }
        if (ACTIVE_CONTENT_TYPES.contains(detectedType)
                || !MEDIA_TYPES_BY_EXTENSION.get(extension).contains(detectedType)) {
            throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
        }
        return new ValidatedArtifact(fileName, detectedType);
    }

    private void validateOfficeContainer(MultipartFile file, String extension) {
        if (OOXML_MAIN_CONTENT_TYPE_BY_EXTENSION.containsKey(extension)) {
            validateOoxmlContainer(file, OOXML_MAIN_CONTENT_TYPE_BY_EXTENSION.get(extension));
        } else if (OLE_ROOT_ENTRIES_BY_EXTENSION.containsKey(extension)) {
            validateOleContainer(file, OLE_ROOT_ENTRIES_BY_EXTENSION.get(extension));
        }
    }

    private void validateOoxmlContainer(MultipartFile file, String expectedMainContentType) {
        Path temporaryFile = null;
        try {
            temporaryFile = copyToTemporaryFile(file);
            validateOoxmlResourceLimits(temporaryFile);
            try (OPCPackage opcPackage =
                    OPCPackage.open(temporaryFile.toFile(), PackageAccess.READ)) {
                if (!hasExpectedMainPart(opcPackage, expectedMainContentType)) {
                    throw new BusinessException(
                            ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
                }
                if (containsActiveOoxmlContent(opcPackage)) {
                    throw new BusinessException(
                            ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
                }
            }
        } catch (BusinessException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
        } finally {
            if (temporaryFile != null) {
                try {
                    Files.deleteIfExists(temporaryFile);
                } catch (IOException ignored) {
                    // The temporary file contains untrusted data and must never be reused. The JVM
                    // temp directory remains the final cleanup boundary if immediate deletion fails.
                    temporaryFile.toFile().deleteOnExit();
                }
            }
        }
    }

    private Path copyToTemporaryFile(MultipartFile file) throws IOException {
        Path temporaryFile = Files.createTempFile("astron-workflow-artifact-", ".ooxml");
        boolean completed = false;
        try (InputStream input = file.getInputStream();
                OutputStream output = Files.newOutputStream(
                        temporaryFile,
                        StandardOpenOption.WRITE,
                        StandardOpenOption.TRUNCATE_EXISTING)) {
            byte[] buffer = new byte[COPY_BUFFER_SIZE];
            long copied = 0;
            int read;
            while ((read = input.read(buffer)) != -1) {
                copied += read;
                if (copied > properties.getArtifactMaxFileSize().toBytes()) {
                    throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_FILE_TOO_LARGE);
                }
                output.write(buffer, 0, read);
            }
            completed = true;
            return temporaryFile;
        } finally {
            if (!completed) {
                Files.deleteIfExists(temporaryFile);
            }
        }
    }

    /**
     * Reads every archive entry through POI's zip-bomb-aware stream before OPC parsing. POI only
     * exposes JVM-global limits, so per-upload absolute limits are enforced here as well to avoid
     * changing the behavior of unrelated Excel import paths in the same application.
     */
    void validateOoxmlResourceLimits(Path file) throws IOException {
        // The JDK implementation reads the compact central directory without materializing POI
        // PackagePart objects. Reject entry floods before constructing POI's ZIP wrapper.
        try (ZipFile centralDirectory = new ZipFile(file.toFile())) {
            if (centralDirectory.size() > ooxmlResourceLimits.maxEntryCount()) {
                throw new IOException("OOXML archive entry count limit exceeded");
            }
        }
        try (ZipSecureFile archive = new ZipSecureFile(file.toFile())) {
            Enumeration<ZipArchiveEntry> entries = archive.getEntries();
            Set<String> normalizedEntryNames = new HashSet<>();
            byte[] buffer = new byte[COPY_BUFFER_SIZE];
            long totalExpandedBytes = 0;
            int entryCount = 0;
            while (entries.hasMoreElements()) {
                ZipArchiveEntry entry = entries.nextElement();
                if (++entryCount > ooxmlResourceLimits.maxEntryCount()
                        || entry.isUnixSymlink()) {
                    throw new IOException("OOXML archive resource limit exceeded");
                }
                String normalizedName = normalizeArchiveEntryName(entry.getName());
                if (!normalizedEntryNames.add(normalizedName)) {
                    throw new IOException("OOXML archive contains duplicate entry names");
                }
                if (entry.isDirectory()) {
                    continue;
                }

                long entryLimit = ooxmlEntryExpandedByteLimit(normalizedName);
                if (entry.getSize() > entryLimit) {
                    throw new IOException("OOXML archive entry is too large");
                }
                long entryExpandedBytes = 0;
                try (InputStream input = archive.getInputStream(entry)) {
                    int read;
                    while ((read = input.read(buffer)) != -1) {
                        entryExpandedBytes += read;
                        totalExpandedBytes += read;
                        if (entryExpandedBytes > entryLimit
                                || totalExpandedBytes > ooxmlResourceLimits.maxTotalExpandedBytes()) {
                            throw new IOException("OOXML archive expanded data limit exceeded");
                        }
                    }
                }
            }
        }
    }

    private String normalizeArchiveEntryName(String entryName) throws IOException {
        if (StringUtils.isBlank(entryName) || StringUtils.contains(entryName, '\0')) {
            throw new IOException("OOXML archive contains an invalid entry name");
        }
        String normalized = entryName.replace('\\', '/');
        if (StringUtils.startsWith(normalized, "/")
                || StringUtils.contains(normalized, ":")) {
            throw new IOException("OOXML archive contains an absolute entry name");
        }
        String[] segments = StringUtils.splitPreserveAllTokens(normalized, '/');
        for (int index = 0; index < segments.length; index++) {
            String segment = segments[index];
            boolean trailingDirectoryMarker = index == segments.length - 1
                    && StringUtils.isEmpty(segment)
                    && StringUtils.endsWith(normalized, "/");
            if ((!trailingDirectoryMarker && StringUtils.isEmpty(segment))
                    || StringUtils.equalsAny(segment, ".", "..")) {
                throw new IOException("OOXML archive contains an unsafe entry name");
            }
        }
        return StringUtils.lowerCase(normalized, Locale.ROOT);
    }

    private boolean isXmlArchiveEntry(String entryName) {
        return StringUtils.endsWithAny(entryName, ".xml", ".rels");
    }

    private long ooxmlEntryExpandedByteLimit(String entryName) {
        if (StringUtils.equals(entryName, "[content_types].xml")
                || StringUtils.endsWith(entryName, ".rels")) {
            // OPC parses package content types and relationships as control data before callers
            // can inspect normal document parts. Keep these attacker-controlled structures much
            // smaller than ordinary document XML so they cannot create a parser memory spike.
            return ooxmlResourceLimits.maxControlXmlEntryBytes();
        }
        return isXmlArchiveEntry(entryName)
                ? ooxmlResourceLimits.maxXmlEntryBytes()
                : ooxmlResourceLimits.maxEntryBytes();
    }

    private boolean hasExpectedMainPart(OPCPackage opcPackage, String expectedMainContentType) {
        return hasExpectedMainPart(
                opcPackage, PackageRelationshipTypes.CORE_DOCUMENT, expectedMainContentType)
                || hasExpectedMainPart(
                        opcPackage,
                        PackageRelationshipTypes.STRICT_CORE_DOCUMENT,
                        expectedMainContentType);
    }

    private boolean hasExpectedMainPart(
            OPCPackage opcPackage, String relationshipType, String expectedMainContentType) {
        for (PackageRelationship relationship : opcPackage.getRelationshipsByType(relationshipType)) {
            if (relationship.getTargetMode() != TargetMode.INTERNAL) {
                continue;
            }
            PackagePart mainPart = opcPackage.getPart(relationship);
            if (mainPart != null
                    && StringUtils.equals(expectedMainContentType, mainPart.getContentType())) {
                return true;
            }
        }
        return false;
    }

    private boolean containsActiveOoxmlContent(OPCPackage opcPackage) throws Exception {
        for (PackageRelationship relationship : opcPackage.getRelationships()) {
            if (relationship.getTargetMode() == TargetMode.EXTERNAL) {
                return true;
            }
        }
        for (PackagePart part : opcPackage.getParts()) {
            String partName = StringUtils.lowerCase(part.getPartName().getName(), Locale.ROOT);
            String contentType = StringUtils.lowerCase(part.getContentType(), Locale.ROOT);
            if (StringUtils.containsAny(
                    partName,
                    "/embeddings/",
                    "/activex/",
                    "vbaproject.bin")
                    || StringUtils.containsAny(
                            contentType,
                            "macroenabled",
                            "vbaproject",
                            "activex",
                            "oleobject")) {
                return true;
            }
            // Relationship parts describe relationships; asking POI for relationships *of* a
            // relationship part is invalid and would reject otherwise ordinary OOXML files.
            if (StringUtils.endsWith(partName, ".rels")
                    || StringUtils.equals(
                            contentType,
                            "application/vnd.openxmlformats-package.relationships+xml")) {
                continue;
            }
            for (PackageRelationship relationship : part.getRelationships()) {
                if (relationship.getTargetMode() == TargetMode.EXTERNAL) {
                    return true;
                }
            }
        }
        return false;
    }

    private void validateOleContainer(MultipartFile file, Set<String> expectedRootEntries) {
        try (InputStream input = file.getInputStream();
                POIFSFileSystem fileSystem = new POIFSFileSystem(input)) {
            DirectoryEntry root = fileSystem.getRoot();
            boolean matches = expectedRootEntries.stream()
                    .anyMatch(root::hasEntryCaseInsensitive);
            if (!matches || containsForbiddenOleEntry(root)) {
                throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
            }
        } catch (BusinessException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
        }
    }

    private boolean containsForbiddenOleEntry(DirectoryEntry root) {
        Deque<OleDirectoryFrame> pending = new ArrayDeque<>();
        pending.push(new OleDirectoryFrame(root, 0));
        int entryCount = 0;
        while (!pending.isEmpty()) {
            OleDirectoryFrame frame = pending.pop();
            Iterator<Entry> entries = frame.directory().getEntries();
            while (entries.hasNext()) {
                Entry entry = entries.next();
                if (++entryCount > MAX_OLE_ENTRY_COUNT) {
                    throw new BusinessException(
                            ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
                }
                String entryName = StringUtils.lowerCase(entry.getName(), Locale.ROOT);
                if (FORBIDDEN_OLE_ENTRY_NAMES.contains(entryName)) {
                    return true;
                }
                if (entry.isDirectoryEntry()) {
                    int childDepth = frame.depth() + 1;
                    if (childDepth > MAX_OLE_DIRECTORY_DEPTH) {
                        throw new BusinessException(
                                ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
                    }
                    pending.push(new OleDirectoryFrame((DirectoryEntry) entry, childDepth));
                }
            }
        }
        return false;
    }

    private String normalizeFileName(String originalFileName) {
        String normalized = StringUtils.defaultString(originalFileName).replace('\\', '/');
        int slash = normalized.lastIndexOf('/');
        if (slash >= 0) {
            normalized = normalized.substring(slash + 1);
        }
        normalized = normalized
                .replaceAll("[\\p{Cntrl}\\\\/:*?\"<>|]+", "_")
                .replaceAll("\\.{2,}", ".")
                .replaceAll("^\\.+", "")
                .trim();
        if (StringUtils.isBlank(normalized) || normalized.length() > MAX_FILE_NAME_LENGTH) {
            throw new BusinessException(ResponseEnum.PARAM_ERROR);
        }
        return normalized;
    }

    private String normalizeMediaType(String contentType) {
        String normalized = StringUtils.lowerCase(StringUtils.trimToEmpty(contentType), Locale.ROOT);
        int separator = normalized.indexOf(';');
        return separator < 0 ? normalized : normalized.substring(0, separator).trim();
    }

    public record ValidatedArtifact(String fileName, String contentType) {}

    record OoxmlResourceLimits(
            int maxEntryCount,
            long maxEntryBytes,
            long maxXmlEntryBytes,
            long maxControlXmlEntryBytes,
            long maxTotalExpandedBytes) {

        OoxmlResourceLimits {
            if (maxEntryCount <= 0
                    || maxEntryBytes <= 0
                    || maxXmlEntryBytes <= 0
                    || maxControlXmlEntryBytes <= 0
                    || maxTotalExpandedBytes <= 0) {
                throw new IllegalArgumentException("OOXML resource limits must be positive");
            }
        }
    }

    private record OleDirectoryFrame(DirectoryEntry directory, int depth) {}
}
