package com.iflytek.astron.console.hub.util.wechat;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Base64;
import java.util.Map;
import org.junit.jupiter.api.Test;

class WXBizMsgCryptTest {

    private WXBizMsgCrypt crypt() throws AesException {
        // Public fixture, never an application credential.
        String key = Base64.getEncoder().withoutPadding().encodeToString(new byte[32]);
        return new WXBizMsgCrypt("fixture-token", key, "fixture-app");
    }

    @Test
    void keepsTheWeChatRandomPrefixFormat() throws Exception {
        assertTrue(crypt().getRandomStr().matches("[A-Za-z0-9]{16}"));
    }

    @Test
    void encryptedRepliesRemainCompatibleWithMessageVerification() throws Exception {
        WXBizMsgCrypt crypt = crypt();
        String plaintext = "<xml><Content>hello 世界</Content></xml>";
        String timestamp = "1700000000";
        String nonce = "fixture-nonce";
        String encrypted = crypt.encryptMsg(plaintext, timestamp, nonce);
        Map<String, String> fields = XMLParse.extract(encrypted, new String[] {"MsgSignature"});

        assertEquals(plaintext,
                crypt.decryptMsg(fields.get("MsgSignature"), timestamp, nonce, encrypted));
        assertThrows(AesException.class,
                () -> crypt.decryptMsg(fields.get("MsgSignature"), timestamp, "wrong-nonce", encrypted));
    }
}
