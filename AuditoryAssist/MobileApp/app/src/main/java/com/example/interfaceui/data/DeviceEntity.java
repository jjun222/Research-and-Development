package com.example.interfaceui.data;

import androidx.room.Entity;
import androidx.room.PrimaryKey;

@Entity(tableName = "devices")
public class DeviceEntity {
    @PrimaryKey(autoGenerate = true)
    public long id;

    public String alias;       // 별칭
    public String topicOrId;   // 토픽 또는 ID
    public String type;        // "publisher" / "subscriber"

    public DeviceEntity(String alias, String topicOrId, String type) {
        this.alias = alias;
        this.topicOrId = topicOrId;
        this.type = type;
    }

    public String getAlias() { return alias; }
    public String getTopicOrId() { return topicOrId; }
    public String getType() { return type; }
}
