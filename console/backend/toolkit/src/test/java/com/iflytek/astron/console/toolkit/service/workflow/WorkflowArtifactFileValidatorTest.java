package com.iflytek.astron.console.toolkit.service.workflow;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.toolkit.config.properties.SkillSandboxArtifactProperties;
import com.iflytek.astron.console.toolkit.service.workflow.WorkflowArtifactFileValidator.OoxmlResourceLimits;
import com.iflytek.astron.console.toolkit.service.workflow.WorkflowArtifactFileValidator.ValidatedArtifact;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import org.apache.poi.poifs.filesystem.DirectoryEntry;
import org.apache.poi.poifs.filesystem.POIFSFileSystem;
import org.apache.poi.xslf.usermodel.XMLSlideShow;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.util.unit.DataSize;

class WorkflowArtifactFileValidatorTest {

    private static final byte[] TEXT_SAMPLE =
            "Astron workflow artifact output.\n".getBytes(StandardCharsets.UTF_8);
    private static final byte[] JSON_SAMPLE =
            "{\"status\":\"ok\",\"count\":2}\n".getBytes(StandardCharsets.UTF_8);
    private static final byte[] PNG_SAMPLE = Base64.getDecoder()
            .decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=");

    private SkillSandboxArtifactProperties properties;
    private WorkflowArtifactFileValidator validator;

    @BeforeEach
    void setUp() {
        properties = new SkillSandboxArtifactProperties();
        properties.setArtifactMaxFileSize(DataSize.ofMegabytes(2));
        validator = new WorkflowArtifactFileValidator(properties);
    }

    @Test
    void springContextWiresProductionConstructor() {
        try (AnnotationConfigApplicationContext context =
                new AnnotationConfigApplicationContext()) {
            context.registerBean(
                    SkillSandboxArtifactProperties.class, () -> properties);
            context.register(WorkflowArtifactFileValidator.class);

            context.refresh();

            assertThat(context.getBean(WorkflowArtifactFileValidator.class)).isNotNull();
        }
    }

    @ParameterizedTest(name = "accepts real {0} artifact")
    @MethodSource("allowedArtifacts")
    void validateAcceptsAllowedArtifacts(
            String fileName, String declaredType, byte[] content, Set<String> detectedTypes) {
        MockMultipartFile file =
                new MockMultipartFile("file", fileName, declaredType, content);

        ValidatedArtifact validated = validator.validate(file);

        assertThat(validated.fileName()).isEqualTo(fileName);
        assertThat(validated.contentType()).isIn(detectedTypes);
    }

    static Stream<Arguments> allowedArtifacts() throws IOException {
        return Stream.of(
                Arguments.of("report.txt", "text/plain; charset=UTF-8", TEXT_SAMPLE, Set.of("text/plain")),
                Arguments.of(
                        "result.json",
                        "application/json",
                        JSON_SAMPLE,
                        Set.of("application/json", "text/plain")),
                Arguments.of("pixel.png", "image/png", PNG_SAMPLE, Set.of("image/png")),
                Arguments.of("report.pdf", "application/pdf", pdfSample(), Set.of("application/pdf")),
                Arguments.of(
                        "report.docx",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        docxSample(),
                        Set.of(
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                "application/x-tika-ooxml",
                                "application/zip")),
                Arguments.of(
                        "report.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        xlsxSample(),
                        Set.of(
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                "application/x-tika-ooxml",
                                "application/zip")),
                Arguments.of(
                        "report.pptx",
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        pptxSample(),
                        Set.of(
                                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                                "application/x-tika-ooxml",
                                "application/zip")));
    }

    @ParameterizedTest(name = "rejects active {0} content")
    @MethodSource("activeContentArtifacts")
    void validateRejectsActiveContent(
            String description, String fileName, String declaredType, byte[] content) {
        MockMultipartFile file =
                new MockMultipartFile("file", fileName, declaredType, content);

        assertRejected(file, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    static Stream<Arguments> activeContentArtifacts() {
        return Stream.of(
                Arguments.of(
                        "HTML disguised as text",
                        "report.txt",
                        "application/octet-stream",
                        "<!doctype html><html><body><script>alert(1)</script></body></html>"
                                .getBytes(StandardCharsets.UTF_8)),
                Arguments.of(
                        "SVG disguised as PNG",
                        "diagram.png",
                        "application/octet-stream",
                        "<svg xmlns=\"http://www.w3.org/2000/svg\"><script>alert(1)</script></svg>"
                                .getBytes(StandardCharsets.UTF_8)),
                Arguments.of(
                        "declared JavaScript",
                        "notes.txt",
                        "application/javascript",
                        "alert(document.domain);".getBytes(StandardCharsets.UTF_8)));
    }

    @Test
    void validateRejectsExtensionDisguisedBinaryContent() {
        MockMultipartFile disguisedPng =
                new MockMultipartFile("file", "invoice.pdf", "application/octet-stream", PNG_SAMPLE);

        assertRejected(disguisedPng, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsOrdinaryZipDisguisedAsDocx() throws IOException {
        MockMultipartFile disguisedZip = new MockMultipartFile(
                "file",
                "invoice.docx",
                "application/octet-stream",
                zipSample("payload.txt", "not an office package"));

        assertRejected(disguisedZip, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsWordPackageDisguisedAsXlsx() throws IOException {
        MockMultipartFile disguisedDocument = new MockMultipartFile(
                "file",
                "invoice.xlsx",
                "application/octet-stream",
                docxSample());

        assertRejected(disguisedDocument, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsOoxmlWithExternalRelationships() throws IOException {
        MockMultipartFile externallyLinkedDocument = new MockMultipartFile(
                "file",
                "report.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                docxWithExternalRelationship());

        assertRejected(
                externallyLinkedDocument,
                ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsMacroPayloadInsideStandardOoxmlContainer() throws IOException {
        MockMultipartFile macroDocument = new MockMultipartFile(
                "file",
                "report.docx",
                "application/octet-stream",
                docxWithVbaProject());

        assertRejected(macroDocument, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsOoxmlWithMoreThanBoundedEntryCount() throws IOException {
        MockMultipartFile entryFlood = new MockMultipartFile(
                "file",
                "report.docx",
                "application/octet-stream",
                docxWithAdditionalEntries(510));

        assertRejected(entryFlood, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsOversizedOoxmlRelationshipControlXml() throws IOException {
        MockMultipartFile relationshipFlood = new MockMultipartFile(
                "file",
                "report.docx",
                "application/octet-stream",
                docxWithRelationshipControlPadding("x".repeat(300 * 1024)));

        assertRejected(relationshipFlood, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void ooxmlPreflightRejectsEntryExpandedBytesBeyondAbsoluteBound() throws IOException {
        OoxmlResourceLimits limits = new OoxmlResourceLimits(10, 64, 64, 64, 1024);

        assertOoxmlPreflightRejected(limits, zipSample("payload.bin", "x".repeat(65)));
    }

    @Test
    void ooxmlPreflightRejectsAggregateExpandedBytesBeyondAbsoluteBound() throws IOException {
        OoxmlResourceLimits limits = new OoxmlResourceLimits(10, 64, 64, 64, 64);

        assertOoxmlPreflightRejected(limits, zipSampleWithEntries(2, "x".repeat(40)));
    }

    @Test
    void ooxmlPreflightUsesTighterRelationshipControlXmlBound() throws IOException {
        OoxmlResourceLimits limits = new OoxmlResourceLimits(10, 1024, 1024, 32, 4096);

        assertOoxmlPreflightRejected(limits, zipSample("_rels/.rels", "x".repeat(33)));
    }

    @Test
    void validateRejectsOleContainerDisguisedAsLegacyWordDocument() throws IOException {
        MockMultipartFile disguisedOle = new MockMultipartFile(
                "file",
                "invoice.doc",
                "application/octet-stream",
                oleSample("Workbook"));

        assertRejected(disguisedOle, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsLegacyOfficeMacroStorage() throws IOException {
        MockMultipartFile macroDocument = new MockMultipartFile(
                "file",
                "report.doc",
                "application/octet-stream",
                oleWordSampleWithMacroStorage());

        assertRejected(macroDocument, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsLegacyOfficeEntryFlood() throws IOException {
        MockMultipartFile entryFlood = new MockMultipartFile(
                "file",
                "report.doc",
                "application/octet-stream",
                oleWordSampleWithDirectories(512));

        assertRejected(entryFlood, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsLegacyOfficeDirectoryDepthFlood() throws IOException {
        MockMultipartFile depthFlood = new MockMultipartFile(
                "file",
                "report.doc",
                "application/octet-stream",
                oleWordSampleWithDirectoryDepth(33));

        assertRejected(depthFlood, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsDeclaredMimeTypeThatDoesNotMatchExtension() {
        MockMultipartFile mismatched =
                new MockMultipartFile("file", "pixel.png", "text/plain", PNG_SAMPLE);

        assertRejected(mismatched, ResponseEnum.WORKFLOW_ARTIFACT_CONTENT_TYPE_MISMATCH);
    }

    @Test
    void validateRejectsFileLargerThanConfiguredMaximum() {
        properties.setArtifactMaxFileSize(DataSize.ofBytes(4));
        MockMultipartFile oversized = new MockMultipartFile(
                "file", "large.txt", "text/plain", "12345".getBytes(StandardCharsets.UTF_8));

        assertRejected(oversized, ResponseEnum.WORKFLOW_ARTIFACT_FILE_TOO_LARGE);
    }

    @Test
    void validateNormalizesPathAndUnsafeFilenameCharacters() {
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "C:\\fakepath\\nested\\report:Q1?..txt",
                "text/plain",
                TEXT_SAMPLE);

        ValidatedArtifact validated = validator.validate(file);

        assertThat(validated.fileName()).isEqualTo("report_Q1_.txt");
        assertThat(validated.contentType()).isEqualTo("text/plain");
    }

    @Test
    void validateRejectsFilenameThatNormalizesToEmpty() {
        MockMultipartFile file = new MockMultipartFile("file", "...", "text/plain", TEXT_SAMPLE);

        assertRejected(file, ResponseEnum.PARAM_ERROR);
    }

    @Test
    void validateRejectsFilenameLongerThan255Characters() {
        String fileName = "a".repeat(252) + ".txt";
        MockMultipartFile file =
                new MockMultipartFile("file", fileName, "text/plain", TEXT_SAMPLE);

        assertRejected(file, ResponseEnum.PARAM_ERROR);
    }

    private void assertRejected(MockMultipartFile file, ResponseEnum expectedResponse) {
        BusinessException exception =
                assertThrows(BusinessException.class, () -> validator.validate(file));
        assertThat(exception.getResponseEnum()).isEqualTo(expectedResponse);
    }

    private void assertOoxmlPreflightRejected(OoxmlResourceLimits limits, byte[] archive)
            throws IOException {
        Path temporaryFile = Files.createTempFile("artifact-validator-test-", ".zip");
        try {
            Files.write(temporaryFile, archive);
            WorkflowArtifactFileValidator limitedValidator =
                    new WorkflowArtifactFileValidator(properties, limits);
            assertThrows(
                    IOException.class,
                    () -> limitedValidator.validateOoxmlResourceLimits(temporaryFile));
        } finally {
            Files.deleteIfExists(temporaryFile);
        }
    }

    private static byte[] pdfSample() {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        List<Integer> offsets = new ArrayList<>();
        writeAscii(output, "%PDF-1.4\n");
        offsets.add(output.size());
        writeAscii(output, "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
        offsets.add(output.size());
        writeAscii(output, "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n");
        offsets.add(output.size());
        writeAscii(
                output,
                "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>\nendobj\n");
        byte[] stream = "BT /F1 12 Tf 20 100 Td (Astron artifact) Tj ET\n"
                .getBytes(StandardCharsets.US_ASCII);
        offsets.add(output.size());
        writeAscii(output, "4 0 obj\n<< /Length " + stream.length + " >>\nstream\n");
        output.writeBytes(stream);
        writeAscii(output, "endstream\nendobj\n");
        int xrefOffset = output.size();
        writeAscii(output, "xref\n0 5\n0000000000 65535 f \n");
        for (int offset : offsets) {
            writeAscii(output, String.format(Locale.ROOT, "%010d 00000 n \n", offset));
        }
        writeAscii(
                output,
                "trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n"
                        + xrefOffset
                        + "\n%%EOF\n");
        return output.toByteArray();
    }

    private static byte[] docxSample() throws IOException {
        return docxSample(null, null, null);
    }

    private static byte[] xlsxSample() throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (XSSFWorkbook workbook = new XSSFWorkbook()) {
            workbook.createSheet("Artifacts").createRow(0).createCell(0).setCellValue("safe");
            workbook.write(output);
        }
        return output.toByteArray();
    }

    private static byte[] pptxSample() throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (XMLSlideShow slideShow = new XMLSlideShow()) {
            slideShow.createSlide();
            slideShow.write(output);
        }
        return output.toByteArray();
    }

    private static byte[] docxWithExternalRelationship() throws IOException {
        return docxSample(
                null,
                "word/_rels/document.xml.rels",
                "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                        + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
                        + "<Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink\" Target=\"https://attacker.invalid/payload\" TargetMode=\"External\"/>"
                        + "</Relationships>");
    }

    private static byte[] docxWithVbaProject() throws IOException {
        return docxSample(
                "<Override PartName=\"/word/vbaProject.bin\" ContentType=\"application/vnd.ms-office.vbaProject\"/>",
                "word/vbaProject.bin",
                "macro payload");
    }

    private static byte[] docxSample(
            String extraContentType, String extraEntryName, String extraEntryValue)
            throws IOException {
        return docxSample(extraContentType, extraEntryName, extraEntryValue, null, 0);
    }

    private static byte[] docxWithAdditionalEntries(int additionalEntryCount) throws IOException {
        return docxSample(null, null, null, null, additionalEntryCount);
    }

    private static byte[] docxWithRelationshipControlPadding(String padding) throws IOException {
        return docxSample(null, null, null, padding, 0);
    }

    private static byte[] docxSample(
            String extraContentType,
            String extraEntryName,
            String extraEntryValue,
            String relationshipControlPadding,
            int additionalEntryCount)
            throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(output, StandardCharsets.UTF_8)) {
            addZipEntry(
                    zip,
                    "[Content_Types].xml",
                    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                            + "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
                            + "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
                            + "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
                            + "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
                            + (extraContentType == null ? "" : extraContentType)
                            + "</Types>");
            addZipEntry(
                    zip,
                    "_rels/.rels",
                    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                            + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
                            + "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>"
                            + (relationshipControlPadding == null
                                    ? ""
                                    : "<!--" + relationshipControlPadding + "-->")
                            + "</Relationships>");
            addZipEntry(
                    zip,
                    "word/document.xml",
                    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                            + "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
                            + "<w:body><w:p><w:r><w:t>Astron artifact</w:t></w:r></w:p></w:body>"
                            + "</w:document>");
            if (extraEntryName != null) {
                addZipEntry(zip, extraEntryName, extraEntryValue);
            }
            for (int index = 0; index < additionalEntryCount; index++) {
                addZipEntry(zip, "custom/entry-" + index + ".bin", "safe");
            }
        }
        return output.toByteArray();
    }

    private static byte[] zipSample(String entryName, String value) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(output, StandardCharsets.UTF_8)) {
            addZipEntry(zip, entryName, value);
        }
        return output.toByteArray();
    }

    private static byte[] zipSampleWithEntries(int entryCount, String value) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(output, StandardCharsets.UTF_8)) {
            for (int index = 0; index < entryCount; index++) {
                addZipEntry(zip, "payload-" + index + ".bin", value);
            }
        }
        return output.toByteArray();
    }

    private static byte[] oleSample(String entryName) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (POIFSFileSystem fileSystem = new POIFSFileSystem()) {
            fileSystem.createDocument(
                    new ByteArrayInputStream("not a legacy office document"
                            .getBytes(StandardCharsets.UTF_8)),
                    entryName);
            fileSystem.writeFilesystem(output);
        }
        return output.toByteArray();
    }

    private static byte[] oleWordSampleWithMacroStorage() throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (POIFSFileSystem fileSystem = new POIFSFileSystem()) {
            fileSystem.createDocument(
                    new ByteArrayInputStream("word document"
                            .getBytes(StandardCharsets.UTF_8)),
                    "WordDocument");
            fileSystem.getRoot().createDirectory("_VBA_PROJECT_CUR");
            fileSystem.writeFilesystem(output);
        }
        return output.toByteArray();
    }

    private static byte[] oleWordSampleWithDirectories(int directoryCount) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (POIFSFileSystem fileSystem = new POIFSFileSystem()) {
            fileSystem.createDocument(
                    new ByteArrayInputStream(
                            "word document".getBytes(StandardCharsets.UTF_8)),
                    "WordDocument");
            for (int index = 0; index < directoryCount; index++) {
                fileSystem.getRoot().createDirectory("dir-" + index);
            }
            fileSystem.writeFilesystem(output);
        }
        return output.toByteArray();
    }

    private static byte[] oleWordSampleWithDirectoryDepth(int directoryDepth)
            throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (POIFSFileSystem fileSystem = new POIFSFileSystem()) {
            fileSystem.createDocument(
                    new ByteArrayInputStream(
                            "word document".getBytes(StandardCharsets.UTF_8)),
                    "WordDocument");
            DirectoryEntry directory = fileSystem.getRoot();
            for (int depth = 0; depth < directoryDepth; depth++) {
                directory = directory.createDirectory("level-" + depth);
            }
            fileSystem.writeFilesystem(output);
        }
        return output.toByteArray();
    }

    private static void addZipEntry(ZipOutputStream zip, String name, String value)
            throws IOException {
        zip.putNextEntry(new ZipEntry(name));
        zip.write(value.getBytes(StandardCharsets.UTF_8));
        zip.closeEntry();
    }

    private static void writeAscii(ByteArrayOutputStream output, String value) {
        output.writeBytes(value.getBytes(StandardCharsets.US_ASCII));
    }
}
