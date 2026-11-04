package com.iflytek.astron.console.hub.platform;

import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/** Register identity metadata only. Tenant and execution grants remain explicit. */
@Service
public class CurrentProfileService {
    private final JdbcTemplate jdbc;

    public CurrentProfileService(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    public Map<String, Object> register(String subject, String displayName) {
        if (subject == null || subject.isBlank() || subject.length() > 128
                || subject.codePoints().anyMatch(Character::isISOControl)) throw new ProfileUnavailable();
        String name = displayName == null || displayName.isBlank() || displayName.length() > 255
                ? subject : displayName;
        // The legacy unique key uses a case/accent-insensitive collation. Do not
        // overwrite a conflicting row, nor revive an existing disabled identity.
        jdbc.update("""
                INSERT INTO user_info(uid,username,nickname,account_status,deleted)
                VALUES(?,?,?,1,0) ON DUPLICATE KEY UPDATE id=id
                """, subject, name, name);
        var rows = jdbc.queryForList("SELECT uid,username,account_status,deleted FROM user_info WHERE uid=?", subject);
        if (rows.size() != 1) throw new ProfileUnavailable();
        var row = rows.getFirst();
        if (!subject.equals(row.get("uid"))
                || !(row.get("account_status") instanceof Number status) || status.intValue() != 1
                || !(row.get("deleted") instanceof Number deleted) || deleted.intValue() != 0)
            throw new ProfileUnavailable();
        return Map.of("uid", subject, "displayName", row.get("username") == null ? subject : row.get("username"));
    }

    public static final class ProfileUnavailable extends RuntimeException {
        public ProfileUnavailable() { super("profile_unavailable"); }
    }
}
