package com.iflytek.astron.console.toolkit.service.extra;

import com.alibaba.fastjson2.*;
import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.response.ApiResult;
import com.iflytek.astron.console.commons.security.WorkflowInternalApiKey;
import com.iflytek.astron.console.toolkit.common.constant.CommonConst;
import com.iflytek.astron.console.toolkit.config.properties.ApiUrl;
import com.iflytek.astron.console.toolkit.config.properties.CommonConfig;
import com.iflytek.astron.console.toolkit.entity.core.workflow.FlowProtocol;
import com.iflytek.astron.console.toolkit.entity.enumVo.DBOperateEnum;
import com.iflytek.astron.console.toolkit.entity.enumVo.DBTableEnvEnum;
import com.iflytek.astron.console.toolkit.util.OkHttpUtil;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.io.*;
import java.net.URL;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.*;


/**
 * Core system service for handling workflow operations, file uploads, and database management
 * Provides integration with external workflow and database services
 */
@Service
@Slf4j
public class CoreSystemService {

    public static final String X_CONSUMER_USERNAME = "X-Consumer-Username";

    public static final String API_PUBLISH_PATH = "/v1/publish";
    public static final String API_AUTH_PATH = "/v1/auth";

    public static final String UPLOAD_FILE_PATH = "/workflow/v1/upload_file";
    public static final String BATCH_UPLOAD_FILE_PATH = "/workflow/v1/upload_files";
    public static final String ADD_COMPARISONS_PATH = "/workflow/v1/protocol/compare/save";
    public static final String GET_COMPARISON_PATH = "/workflow/v1/protocol/compare/get";
    public static final String DELETE_COMPARISONS_PATH = "/workflow/v1/protocol/compare/delete";
    public static final String CREATE_DATABASE_PATH = "/xingchen-db/v1/create_database";
    public static final String EXEC_DDL_PATH = "/xingchen-db/v1/exec_ddl";
    public static final String EXEC_DML_PATH = "/xingchen-db/v1/exec_dml";
    public static final String UPLOAD_DATA_PATH = "/xingchen-db/v1/upload_data";
    public static final String CLONE_DATABASE_PATH = "/xingchen-db/v1/clone_database";
    public static final String DROP_DATABASE_PATH = "/xingchen-db/v1/drop_database";
    public static final String MODIFY_DATABASE_PATH = "/xingchen-db/v1/modify_db_description";



    @Resource
    ApiUrl apiUrl;

    @Value("${spring.profiles.active}")
    String env;

    @Value("${workflow.internal-api-key:}")
    String workflowInternalApiKey;

    @Autowired
    AppService appService;
    @Autowired
    private CommonConfig commonConfig;

    /**
     * Publish workflow with specified configuration
     *
     * @param flowId The workflow ID to publish
     * @param plat Platform identifier
     * @param status Release status
     * @param version Workflow version (optional)
     * @throws BusinessException if publish operation fails
     */
    public void publish(String flowId, int plat, int status, String version) {
        Map<String, String> requestHeader = new HashMap<>();
        String url = apiUrl.getWorkflow().concat(API_PUBLISH_PATH);
        JSONObject jsonObject = new JSONObject()
                .fluentPut("flow_id", flowId)
                .fluentPut("release_status", status)
                .fluentPut("data", null)
                .fluentPut("plat", plat);
        if (StringUtils.isNotBlank(version)) {
            jsonObject.fluentPut("version", version);
        }
        String body = jsonObject.toString();

        if (!StringUtils.equalsAny(env, CommonConst.ENV_DEV)) {
            requestHeader = assembleRequestHeader(url, apiUrl.getTenantKey(), apiUrl.getTenantSecret(), "POST", body.getBytes(StandardCharsets.UTF_8));
        }
        requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
        addWorkflowInternalApiKey(requestHeader);
        log.info(
                "workflow protocol publish, url = {}, flowId = {}, bodyBytes = {}",
                url,
                flowId,
                body.getBytes(StandardCharsets.UTF_8).length);
        String response = OkHttpUtil.post(url, requestHeader, body);
        ApiResult<?> result = parseApiResult(response, "workflow protocol publish", url);
        log.info(
                "workflow protocol publish response, url = {}, flowId = {}, statusCode = {}",
                url,
                flowId,
                result == null ? null : result.code());
        if (result == null) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
        if (result.code() != 0) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }


    /**
     * Authorize workflow access for specified application
     *
     * @param flowId The workflow ID to authorize
     * @param appId Application ID requesting access
     * @param plat Platform identifier
     * @throws BusinessException if authorization fails
     */
    public void auth(String flowId, String appId, int plat) {
        Map<String, String> requestHeader = new HashMap<>();

        String authUrl = apiUrl.getWorkflow().concat(API_AUTH_PATH);
        JSONObject authJson = new JSONObject()
                .fluentPut("flow_id", flowId);


        if (StringUtils.equalsAny(env, CommonConst.ENV_DEV)) {
            authJson.fluentPut("app_id", "a01c2bc7");
        } else {
            authJson.fluentPut("app_id", appId);
            if (!StringUtils.equalsAny(env, CommonConst.ENV_DEV)) {
                requestHeader = assembleRequestHeader(authUrl, apiUrl.getTenantKey(), apiUrl.getTenantSecret(), "POST",
                        authJson.toString().getBytes(StandardCharsets.UTF_8));
            }
        }
        String authBody = authJson.toString();
        requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
        addWorkflowInternalApiKey(requestHeader);
        log.info(
                "workflow protocol auth, url = {}, flowId = {}, bodyBytes = {}",
                authUrl,
                flowId,
                authBody.getBytes(StandardCharsets.UTF_8).length);
        String authResponse = OkHttpUtil.post(authUrl, requestHeader, authBody);
        ApiResult<?> authResult = parseApiResult(authResponse, "workflow protocol auth", authUrl);
        log.info(
                "workflow protocol auth response, url = {}, flowId = {}, statusCode = {}",
                authUrl,
                flowId,
                authResult == null ? null : authResult.code());
        if (authResult == null) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
        if (authResult.code() != 0) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    /**
     * Upload single file to workflow system
     *
     * @param file The multipart file to upload
     * @param apiKey API key for authentication
     * @param apiSecret API secret for authentication
     * @return File URL after successful upload
     * @throws BusinessException if upload fails
     */
    public String uploadFile(MultipartFile file, String apiKey, String apiSecret) {
        Map<String, String> requestHeader = new HashMap<>();
        String uploadUrl = apiUrl.getWorkflow().concat(UPLOAD_FILE_PATH);
        // Pass file via form-data
        Map<String, Object> param = new HashMap<>();
        param.put("file", file);
        try {
            requestHeader = assembleRequestHeader(uploadUrl, apiKey, apiSecret, "POST", convertMapToBytes(param));
            requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
            addWorkflowInternalApiKey(requestHeader);
            requestHeader.put("Content-Type", "multipart/form-data");
            log.info(
                    "workflow protocol upload file, url = {}, fileBytes = {}",
                    uploadUrl,
                    file.getSize());
            String authResponse = OkHttpUtil.postMultipart(uploadUrl, requestHeader, null, param);
            ApiResult<?> authResult = parseApiResult(authResponse, "workflow protocol upload file", uploadUrl);
            log.info(
                    "workflow protocol upload file response, url = {}, statusCode = {}",
                    uploadUrl,
                    authResult == null ? null : authResult.code());
            if (authResult == null) {
                throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
            }
            if (authResult.code() != 0) {
                throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
            }
            return ((Map<String, String>) authResult.data()).get("url");
        } catch (Exception ex) {
            log.error(
                    "workflow protocol upload file failed, url = {}, errorType = {}",
                    uploadUrl,
                    ex.getClass().getSimpleName());
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    /**
     * Upload multiple files to workflow system
     *
     * @param files Array of multipart files to upload
     * @param apiKey API key for authentication
     * @param apiSecret API secret for authentication
     * @return List of file URLs after successful upload
     * @throws BusinessException if upload fails
     */
    public List<String> batchUploadFile(MultipartFile[] files, String apiKey, String apiSecret) {
        Map<String, String> requestHeader = new HashMap<>();
        String authUrl = apiUrl.getWorkflow().concat(BATCH_UPLOAD_FILE_PATH);
        // Pass files via form-data
        Map<String, Object> param = new HashMap<>();
        param.put("files", files);
        try {
            requestHeader = assembleRequestHeader(authUrl, apiKey, apiSecret, "POST", convertMapToBytes(param));
            requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
            addWorkflowInternalApiKey(requestHeader);
            requestHeader.put("Content-Type", "multipart/form-data");
            log.info(
                    "workflow protocol upload files, url = {}, fileCount = {}",
                    authUrl,
                    files == null ? 0 : files.length);
            String authResponse = OkHttpUtil.postMultipart(authUrl, requestHeader, null, param);
            ApiResult<?> authResult = parseApiResult(authResponse, "workflow protocol upload files", authUrl);
            log.info(
                    "workflow protocol upload files response, url = {}, statusCode = {}",
                    authUrl,
                    authResult == null ? null : authResult.code());
            if (authResult == null) {
                throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
            }
            if (authResult.code() != 0) {
                throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
            }
            return ((Map<String, List<String>>) authResult.data()).get("urls");
        } catch (Exception ex) {
            log.error(
                    "workflow protocol upload files failed, url = {}, fileCount = {}, errorType = {}",
                    authUrl,
                    files == null ? 0 : files.length,
                    ex.getClass().getSimpleName());
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    /**
     * Converts a map containing MultipartFile objects to byte array for serialization Handles both
     * single MultipartFile and MultipartFile array values
     *
     * @param map The map containing parameters including MultipartFile objects
     * @return Serialized byte array representation of the map
     * @throws IOException if conversion fails or file reading errors occur
     */
    private byte[] convertMapToBytes(Map<String, Object> map) throws IOException {
        try (ByteArrayOutputStream byteArrayOutputStream = new ByteArrayOutputStream();
                ObjectOutputStream objectOutputStream = new ObjectOutputStream(byteArrayOutputStream)) {

            Map<String, Object> serializableMap = new HashMap<>();

            for (Map.Entry<String, Object> entry : map.entrySet()) {
                Object value = entry.getValue();
                if (value instanceof MultipartFile) {
                    // Convert single MultipartFile to byte array
                    MultipartFile multipartFile = (MultipartFile) value;
                    serializableMap.put(entry.getKey(), multipartFile.getBytes());
                } else if (value instanceof MultipartFile[]) {
                    // Convert MultipartFile[] to byte[][]
                    MultipartFile[] multipartFiles = (MultipartFile[]) value;
                    byte[][] fileBytes = new byte[multipartFiles.length][];
                    for (int i = 0; i < multipartFiles.length; i++) {
                        fileBytes[i] = multipartFiles[i].getBytes();
                    }
                    serializableMap.put(entry.getKey(), fileBytes);
                } else {
                    // Handle other types normally
                    serializableMap.put(entry.getKey(), value);
                }
            }

            objectOutputStream.writeObject(serializableMap);
            return byteArrayOutputStream.toByteArray();
        }
    }

    /**
     * Calculate header parameters required for signature (HTTP interface)
     *
     * @param requestUrl like 'http://rest-api.xfyun.cn/v2/iat'
     * @param apiKey API key for authentication
     * @param apiSecret API secret for authentication
     * @param method request method POST/GET/PATCH/DELETE etc....
     * @param body http request body
     * @return header map, contains all headers should be set when access api
     * @throws BusinessException if header assembly fails
     */
    public Map<String, String> assembleRequestHeader(String requestUrl, String apiKey, String apiSecret, String method, byte[] body) {
        URL url = null;
        try {
            url = new URL(requestUrl);
            // Get date
            SimpleDateFormat format = new SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss z", Locale.US);
            format.setTimeZone(TimeZone.getTimeZone("UTC"));
            String date = format.format(new Date());
            // Calculate body digest (SHA256)
            MessageDigest instance = MessageDigest.getInstance("SHA-256");
            instance.update(body);
            String digest = "SHA256=" + Base64.getEncoder().encodeToString(instance.digest());
            // date = "Thu, 19 Dec 2024 07:47:57 GMT";
            String host = url.getHost();
            int port = url.getPort(); // port >0 means url contains port
            if (port > 0) {
                host = host + ":" + port;
            }
            String path = url.getPath();
            if ("".equals(path) || path == null) {
                path = "/";
            }
            // Build parameters required for signature calculation
            StringBuilder builder = new StringBuilder().append("host: ").append(host).append("\n").//
                    append("date: ").append(date).append("\n").//
                    append(method).append(" ").append(path).append(" HTTP/1.1").append("\n").append("digest: ").append(digest);
            Charset charset = Charset.forName("UTF-8");

            // Use hmac-sha256 to calculate signature
            Mac mac = Mac.getInstance("hmacsha256");
            SecretKeySpec spec = new SecretKeySpec(apiSecret.getBytes(charset), "hmacsha256");
            mac.init(spec);
            byte[] hexDigits = mac.doFinal(builder.toString().getBytes(charset));
            String sha = Base64.getEncoder().encodeToString(hexDigits);
            // Build header
            String authorization = String.format("hmac-auth api_key=\"%s\", algorithm=\"%s\", headers=\"%s\", signature=\"%s\"", apiKey, "hmac-sha256", "host date request-line digest", sha);
            Map<String, String> header = new HashMap<String, String>();
            header.put("authorization", authorization);
            header.put("host", host);
            header.put("date", date);
            header.put("digest", digest);
            return header;
        } catch (Exception e) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED, "assemble requestHeader  error:" + e.getMessage());
        }
    }

    /**
     * Adds workflow comparison data for protocol validation Saves comparison protocols for the
     * specified workflow and version
     *
     * @param protocol The flow protocol containing comparison data
     * @param flowId The workflow ID to add comparisons for
     * @param version The specific version of the workflow
     * @throws BusinessException if the add operation fails
     */
    public void addComparisons(FlowProtocol protocol, String flowId, String version) {
        String url = apiUrl.getWorkflow().concat(ADD_COMPARISONS_PATH);
        JSONObject jsonObject = new JSONObject()
                .fluentPut("flow_id", flowId)
                .fluentPut("version", version)
                .fluentPut("data", protocol);

        String body = jsonObject.toString();

        log.info(
                "workflow add comparisons, url = {}, flowId = {}, version = {}, bodyBytes = {}",
                url,
                flowId,
                version,
                body.getBytes(StandardCharsets.UTF_8).length);
        String response = OkHttpUtil.post(url, workflowInternalHeaders(), body);
        ApiResult<?> result = parseApiResult(response, "workflow add comparisons", url);
        log.info(
                "workflow add comparisons response, url = {}, flowId = {}, statusCode = {}",
                url,
                flowId,
                result == null ? null : result.code());
        if (result == null) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
        if (result.code() != 0) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    /**
     * Loads one exact comparison snapshot from Core for a fresh caller-scope authorization check. The
     * original flow ID and comparison version are both required so a caller cannot substitute a row
     * from another workflow group.
     */
    public JSONObject getComparison(String flowId, String version) {
        String url = apiUrl.getWorkflow().concat(GET_COMPARISON_PATH);
        String body = new JSONObject()
                .fluentPut("flow_id", flowId)
                .fluentPut("version", version)
                .toString();
        String response = OkHttpUtil.post(url, workflowInternalHeaders(), body);
        JSONObject result = parseJsonObject(response, "workflow get comparison", url);
        log.info(
                "workflow get comparison response, url = {}, flowId = {}, statusCode = {}",
                url,
                flowId,
                result == null ? null : result.getInteger("code"));
        if (result == null || result.getIntValue("code") != 0
                || !(result.get("data") instanceof Map<?, ?>)) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
        return result.getJSONObject("data");
    }

    /**
     * Deletes workflow comparison data for the specified workflow and version Removes previously saved
     * comparison protocols
     *
     * @param flowId The workflow ID to delete comparisons for
     * @param version The specific version of the workflow
     * @throws BusinessException if the delete operation fails
     */
    public void deleteComparisons(String flowId, String version) {
        String url = apiUrl.getWorkflow().concat(DELETE_COMPARISONS_PATH);

        JSONObject jsonObject = new JSONObject()
                .fluentPut("flow_id", flowId)
                .fluentPut("version", version);

        String body = jsonObject.toString();

        log.info(
                "workflow delete comparisons, url = {}, flowId = {}, version = {}, bodyBytes = {}",
                url,
                flowId,
                version,
                body.getBytes(StandardCharsets.UTF_8).length);
        String response = OkHttpUtil.delete(url, workflowInternalHeaders(), body);
        ApiResult<?> result = parseApiResult(response, "workflow delete comparisons", url);
        log.info(
                "workflow delete comparisons response, url = {}, flowId = {}, statusCode = {}",
                url,
                flowId,
                result == null ? null : result.code());
        if (result == null) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
        if (result.code() != 0) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    private Map<String, String> workflowInternalHeaders() {
        Map<String, String> headers = new HashMap<>();
        addWorkflowInternalApiKey(headers);
        return headers;
    }

    private void addWorkflowInternalApiKey(Map<String, String> headers) {
        headers.put(
                WorkflowInternalApiKey.HEADER,
                WorkflowInternalApiKey.requireConfigured(workflowInternalApiKey));
    }


    /**
     * Creates a new database in the SparkDB system
     *
     * @param databaseName The name of the database to create
     * @param uid User identifier for ownership
     * @param spaceId Optional space ID for database organization (can be null)
     * @param description Optional description of the database
     * @return The unique database ID of the created database
     * @throws BusinessException if database creation fails
     */
    public Long createDatabase(String databaseName, String uid, Long spaceId, String description) {
        Map<String, String> requestHeader = new HashMap<>();
        String url = apiUrl.getSparkDB().concat(CREATE_DATABASE_PATH);
        JSONObject params = new JSONObject()
                .fluentPut("database_name", databaseName)
                .fluentPut("uid", uid)
                .fluentPut("description", description);
        if (spaceId != null) {
            params.fluentPut("space_id", spaceId.toString());
        }
        String body = params.toString();
        requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
        log.info(
                "create database, url = {}, spaceId = {}, bodyBytes = {}",
                url,
                spaceId,
                body.getBytes(StandardCharsets.UTF_8).length);
        String response = OkHttpUtil.post(url, requestHeader, body);
        ApiResult<?> result = parseApiResult(response, "create database", url);
        log.info(
                "create database response, url = {}, spaceId = {}, statusCode = {}",
                url,
                spaceId,
                result == null ? null : result.code());
        if (result == null || result.code() != 0) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
        return ((Map<String, Long>) result.data()).get("database_id");
    }

    /**
     * Executes Data Definition Language (DDL) statements on the specified database Used for creating,
     * altering, or dropping database schema objects
     *
     * @param ddl The DDL statement to execute (CREATE, ALTER, DROP, etc.)
     * @param uid User identifier for authorization
     * @param spaceId Optional space ID for context (can be null)
     * @param databaseId The target database ID
     * @throws BusinessException if DDL execution fails
     */
    public void execDDL(String ddl, String uid, Long spaceId, Long databaseId) {
        Map<String, String> requestHeader = new HashMap<>();
        String url = apiUrl.getSparkDB().concat(EXEC_DDL_PATH);
        JSONObject params = new JSONObject()
                .fluentPut("database_id", databaseId)
                .fluentPut("uid", uid)
                .fluentPut("ddl", ddl);
        if (spaceId != null) {
            params.fluentPut("space_id", spaceId.toString());
        }
        String body = params.toString();
        requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
        log.info(
                "exec ddl, url = {}, databaseId = {}, spaceId = {}, bodyBytes = {}",
                url,
                databaseId,
                spaceId,
                body.getBytes(StandardCharsets.UTF_8).length);
        String response = OkHttpUtil.post(url, requestHeader, body);
        ApiResult<?> result = parseApiResult(response, "exec ddl", url);
        log.info(
                "exec ddl response, url = {}, databaseId = {}, statusCode = {}",
                url,
                databaseId,
                result == null ? null : result.code());
        if (result == null || result.code() != 0) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    /**
     * Executes Data Manipulation Language (DML) statements on the specified database Supports SELECT,
     * INSERT, UPDATE, DELETE operations with different return types
     *
     * @param dml The DML statement to execute
     * @param uid User identifier for authorization
     * @param spaceId Optional space ID for context (can be null)
     * @param databaseId The target database ID
     * @param operateType The type of operation (SELECT, INSERT, UPDATE, DELETE)
     * @param execEnv Execution environment (development, testing, production)
     * @return For SELECT operations: List of JSONObject results; For SELECT_TOTAL_COUNT: Long count;
     *         For others: null
     * @throws BusinessException if DML execution fails or result parsing errors occur
     */
    public Object execDML(String dml, String uid, Long spaceId, Long databaseId, Integer operateType, Integer execEnv) {
        Map<String, String> requestHeader = new HashMap<>();
        String url = apiUrl.getSparkDB().concat(EXEC_DML_PATH);
        JSONObject params = new JSONObject()
                .fluentPut("app_id", commonConfig.getAppId())
                .fluentPut("database_id", databaseId)
                .fluentPut("uid", uid)
                .fluentPut("dml", dml)
                .fluentPut("env", DBTableEnvEnum.getByCode(execEnv));
        if (spaceId != null) {
            params.fluentPut("space_id", spaceId.toString());
        }
        String body = params.toString();
        requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
        log.info(
                "exec dml, url = {}, databaseId = {}, operateType = {}, execEnv = {}, bodyBytes = {}",
                url,
                databaseId,
                operateType,
                execEnv,
                body.getBytes(StandardCharsets.UTF_8).length);
        String response = OkHttpUtil.post(url, requestHeader, body);
        ApiResult<?> result = parseApiResult(response, "exec dml", url);
        log.info(
                "exec dml response, url = {}, databaseId = {}, operateType = {}, statusCode = {}",
                url,
                databaseId,
                operateType,
                result == null ? null : result.code());
        if (result == null || result.code() != 0) {
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
        try {
            if (DBOperateEnum.SELECT.getCode().equals(operateType)) {
                // Get data object
                Map<String, Object> data = (Map<String, Object>) result.data();
                JSONArray dataList = JSONArray.parseArray(data.get("exec_success").toString());
                List<JSONObject> searchData = new LinkedList<>();
                dataList.forEach(item -> {
                    JSONObject jsonObject = (JSONObject) item;
                    String[] split = jsonObject.get("uid").toString().split(":");
                    jsonObject.put("uid", split[split.length - 1]);
                    jsonObject.put("id", jsonObject.get("id").toString());
                    searchData.add(jsonObject);
                });
                return searchData;
            } else if (DBOperateEnum.SELECT_TOTAL_COUNT.getCode().equals(operateType)) {
                Map<String, Object> data = (Map<String, Object>) result.data();
                JSONArray dataList = JSONArray.parseArray(data.get("exec_success").toString());
                JSONObject countResult = (JSONObject) dataList.get(0);
                // Support both "count" (with AS count) and "COUNT(*)" (MySQL default column name)
                Object countVal = countResult.get("count");
                if (countVal == null) {
                    countVal = countResult.get("COUNT(*)");
                }
                return Long.valueOf(countVal != null ? countVal.toString() : "0");
            } else {
                return null;
            }
        } catch (Exception ex) {
            log.warn(
                    "exec dml response parsing failed, databaseId = {}, operateType = {}, errorType = {}",
                    databaseId,
                    operateType,
                    ex.getClass().getSimpleName());
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    /**
     * Creates a copy of an existing database with a new name Clones all schema and data from the source
     * database
     *
     * @param dbId The source database ID to clone from
     * @param dbName The name for the new cloned database
     * @param uid User identifier for ownership of the new database
     * @return The unique database ID of the cloned database
     * @throws BusinessException if cloning operation fails
     */
    public Long cloneDataBase(Long dbId, String dbName, String uid) {
        Map<String, String> requestHeader = new HashMap<>();
        String cloneUrl = apiUrl.getSparkDB().concat(CLONE_DATABASE_PATH);
        String body = new JSONObject()
                .fluentPut("database_id", dbId)
                .fluentPut("uid", uid)
                .fluentPut("new_database_name", dbName)
                .toString();
        try {
            requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
            log.info(
                    "clone database, url = {}, databaseId = {}, bodyBytes = {}",
                    cloneUrl,
                    dbId,
                    body.getBytes(StandardCharsets.UTF_8).length);
            String response = OkHttpUtil.post(cloneUrl, requestHeader, body);
            ApiResult<?> result = parseApiResult(response, "clone database", cloneUrl);
            log.info(
                    "clone database response, url = {}, databaseId = {}, statusCode = {}",
                    cloneUrl,
                    dbId,
                    result == null ? null : result.code());
            if (result == null || result.code() != 0) {
                throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
            }
            return ((Map<String, Long>) result.data()).get("database_id");
        } catch (Exception ex) {
            log.error(
                    "clone database failed, databaseId = {}, errorType = {}",
                    dbId,
                    ex.getClass().getSimpleName());
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    /**
     * Permanently deletes a database and all its data This operation is irreversible and will remove
     * all tables and data
     *
     * @param dbId The database ID to delete
     * @param uid User identifier for authorization
     * @throws BusinessException if drop operation fails
     */
    public void dropDataBase(Long dbId, String uid) {
        Map<String, String> requestHeader = new HashMap<>();
        String dropUrl = apiUrl.getSparkDB().concat(DROP_DATABASE_PATH);
        String body = new JSONObject()
                .fluentPut("database_id", dbId)
                .fluentPut("uid", uid)
                .toString();
        try {
            requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
            log.info(
                    "drop database, url = {}, databaseId = {}, bodyBytes = {}",
                    dropUrl,
                    dbId,
                    body.getBytes(StandardCharsets.UTF_8).length);
            String response = OkHttpUtil.post(dropUrl, requestHeader, body);
            ApiResult<?> result = parseApiResult(response, "drop database", dropUrl);
            log.info(
                    "drop database response, url = {}, databaseId = {}, statusCode = {}",
                    dropUrl,
                    dbId,
                    result == null ? null : result.code());
            if (result == null || result.code() != 0) {
                throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
            }
        } catch (Exception ex) {
            log.error(
                    "drop database failed, databaseId = {}, errorType = {}",
                    dbId,
                    ex.getClass().getSimpleName());
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    /**
     * Modifies the description of an existing database Updates metadata without affecting database
     * structure or data
     *
     * @param dbId The database ID to modify
     * @param uid User identifier for authorization
     * @param description The new description for the database
     * @throws BusinessException if modification operation fails
     */
    public void modifyDataBase(Long dbId, String uid, String description) {
        Map<String, String> requestHeader = new HashMap<>();
        String modifyUrl = apiUrl.getSparkDB().concat(MODIFY_DATABASE_PATH);
        String body = new JSONObject()
                .fluentPut("database_id", dbId)
                .fluentPut("uid", uid)
                .fluentPut("description", description)
                .toString();
        try {
            requestHeader.put(X_CONSUMER_USERNAME, apiUrl.getTenantId());
            log.info(
                    "modify database, url = {}, databaseId = {}, bodyBytes = {}",
                    modifyUrl,
                    dbId,
                    body.getBytes(StandardCharsets.UTF_8).length);
            String response = OkHttpUtil.post(modifyUrl, requestHeader, body);
            ApiResult<?> result = parseApiResult(response, "modify database", modifyUrl);
            log.info(
                    "modify database response, url = {}, databaseId = {}, statusCode = {}",
                    modifyUrl,
                    dbId,
                    result == null ? null : result.code());
            if (result == null || result.code() != 0) {
                throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
            }
        } catch (Exception ex) {
            log.error(
                    "modify database failed, databaseId = {}, errorType = {}",
                    dbId,
                    ex.getClass().getSimpleName());
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    private ApiResult<?> parseApiResult(String response, String operation, String url) {
        try {
            return JSON.parseObject(response, ApiResult.class);
        } catch (RuntimeException exception) {
            log.warn(
                    "{} response parse failed, url = {}, bodyBytes = {}, errorType = {}",
                    operation,
                    url,
                    utf8Length(response),
                    exception.getClass().getSimpleName());
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    private JSONObject parseJsonObject(String response, String operation, String url) {
        try {
            return JSON.parseObject(response);
        } catch (RuntimeException exception) {
            log.warn(
                    "{} response parse failed, url = {}, bodyBytes = {}, errorType = {}",
                    operation,
                    url,
                    utf8Length(response),
                    exception.getClass().getSimpleName());
            throw new BusinessException(ResponseEnum.RESPONSE_FAILED);
        }
    }

    private int utf8Length(String value) {
        return value == null ? 0 : value.getBytes(StandardCharsets.UTF_8).length;
    }
}
