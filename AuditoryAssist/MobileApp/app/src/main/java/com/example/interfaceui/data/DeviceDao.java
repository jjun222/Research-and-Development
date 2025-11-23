package com.example.interfaceui.data;

import androidx.room.Dao;
import androidx.room.Delete;
import androidx.room.Insert;
import androidx.room.OnConflictStrategy;
import androidx.room.Query;

import java.util.List;

@Dao
public interface DeviceDao {

    @Query("SELECT * FROM devices ORDER BY alias ASC")
    List<DeviceEntity> getAll();

    @Query("SELECT * FROM devices WHERE id = :id LIMIT 1")
    DeviceEntity getById(long id);

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    long upsert(DeviceEntity device);

    @Delete
    void delete(DeviceEntity device);
}
