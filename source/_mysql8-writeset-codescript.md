# WriteSet Based Replication 相关代码片段

# 1. store_commit_parent

*5.7.12*

```java
// sql/binlog.cc 
TC_LOG::enum_result MYSQL_BIN_LOG::commit(THD *thd, bool all){
    // ....
    if( !cache_mngr->stmt_cache.is_binlog_empty() ){
        trn_ctx->store_commit_parent(max_committed_transaction.get_timestamp())
        //....
    }
}

//另外有
static int binlog_prepare(handlerton *hton, THD *thd, bool all){
    if (!all)  {
        Logical_clock& clock= mysql_bin_log.max_committed_transaction;
        thd->get_transaction()->
        store_commit_parent(clock.get_timestamp());
    }
    //....
}
// in 8.0 
static int binlog_prepare(handlerton *, THD *thd, bool all) { 
  if (!all) {
    thd->get_transaction()->store_commit_parent(
        mysql_bin_log.m_dependency_tracker.get_max_committed_timestamp());
  }
}


static int binlog_init(void *p){
    //...
    binlog_hton->prepare= binlog_prepare;
    //....
}
static handlerton *binlog_hton;
```


*Group Commit相关代码*
```java
// 调用逻辑
ha_comimt_trans()
    -> tc_log->prepare()
        -> ha_prepare_low()
            -> for() {
                ht -> prepare() // 依次调用所有存储引擎的
            }

    -> tc_log->commit() // MYSQL_BINLOG_LOG::commit()
        -> MYSQL_BIN_LOG::ordered_commit()
            // # step 1. flush binlog to io cache.
            -> if(has_commit_order_manager(thd)){ // on slave 
                
            } else { // on master
                if(change_stage(thd, Stage_manager::FLUSH_STATE, thd, NULL, &LOG_log)){
                    // 
                    finish_commit(thd)
                }
            }
            flush_error = process_flush_stage_quene(&total_bytes, &do_rotate, &wait_quene)
            if( flush_error == 0 && total_bytes > 0 ){
                flush_error = flush_cache_to_file(&flush_end_pos)
            }
            update_binlog_end_pos_after_sync = (get_sync_period() == 1) // true if variables.sync_binlog=1
            if(!update_binlog_end_pos_after_sync){ 
                update_binlog_end_pos();  // 如果 sync_binlog != 1 flush 完成后立即给 dump 线程发信号
            }

            // # step2. sync binlog to disk 
            if( change_stage(thd, Stage_manager::SYNC_STAGE, wait_queue, &LOCK_log, &LOCK_sync) ){
                finish_commit(thd)
            }
            
            if(!flush_error && (sync_count+1 > get_sync_period)){
                stage_manager.wait_count_or_timeout(
                    opt_binlog_group_commit_sync_no_delay_count,
                    opt_binlog_group_commit_sync_delay, Stage_manager::SYNC_STAGE);
                // 系统参数: binlog_group_commit_sync_delay
                // binlog_group_commit_sync_no_delay_count 控制的sync延迟，提供组提交吞吐率
            }
            final_queue = stage_manager.fetch_queue_for(Stage_manager::SYNC_STAGE);

            if( flush_error == 0 && total_bytes > 0 ){
                std::pair<bool, bool> result = sync_binlog_file(false)
                sync_error = result.first;
            }
            if( update_binlog_end_pos_after_sync){
                // 执行 update_binlog_end_pos
            }

            // # step3. commit all transaction
            leave_mutex_before_commit_stage = &LOCK_sync;
            if(opt_binlog_order_commits && (sync_error == 0 || binlog_error_action != ABORT_SERVER)){
                if(change_stage(thd, Stage_manger::COMMIT, final_queue,  leave_mutex_before_commit_stage, &LOCK_commit)){
                    finish_commit(thd)
                }
                THD *commit_queue = stage_manager.fetch_queue_for(Stage_manager::COMMIT_STAGE);
                process_commit_stage_queue(thd, commit_queue);
                mysql_mutex_unlock(&LOCK_commit);
                process_after_commit_stage_queue(thd, commit_queue);
                final_queue = commit_queue;
            } else {
                //....
            }
```


*WriteSet or MTS相关代码*

```java
int MYSQL_BIN_LOG::process_flush_stage_quene(
    my_off_t *total_bytes_var,bool *rotate_var,  THD **out_queue_var){
    THD *first_seen = stage_manager.fetch_queue_for(Stage_manager::FLUSH_STAGE);
    assign_automatic_gtids_to_flush_group(first_seen);
    ha_flush_logs(NULL, true);
    for(){
        std::pair<int, my_off_t> result = flush_thread_caches(head); 
    }                                        
}

std::pair<int, my_off_t> MYSQL_BIN_LOG::flush_thread_caches(THD *thd) {
    binlog_cache_mngr *cache_mngr = thd_get_cache_mngr(thd);
    int error = cache_mngr->flush(thd, &bytes, &wrote_xid /*false*/);
}

class binlog_cache_mngr{
    int flush(THD *thd, my_off_t *bytes_written, bool *wrote_xid) {
        stmt_cache.flush(thd, &stmt_bytes, wrote_xid);
        if (int error = trx_cache.flush(thd, &trx_bytes, wrote_xid)) return error;
        *bytes_written = stmt_bytes + trx_bytes;
        //....
    }
}


int binlog_cache_data::flush(
    THD *thd, my_off_t *bytes_written, bool *wrote_xid) {
    if (flags.finalized) {
        Transaction_ctx *trn_ctx = thd->get_transaction();
        trn_ctx->sequence_number = mysql_bin_log.m_dependency_tracker.step();
        if (trn_ctx->last_committed == SEQ_UNINIT)
            trn_ctx->last_committed = trn_ctx->sequence_number - 1;
        // ....
        Binlog_event_writer writer(mysql_bin_log.get_log_file());
        //.....
            mysql_bin_log.write_gtid(thd, this, &writer)
    }
}

bool MYSQL_BIN_LOG::write_gtid(
    THD *thd, binlog_cache_data *cache_data, Binlog_event_writer *writer) {
    int64 sequence_number, last_committed;
    m_dependency_tracker.get_dependency(thd, sequence_number, last_committed);

    thd->get_transaction()->last_committed = SEQ_UNINIT;
    /*
        Generate and write the Gtid_log_event.
    */
    Gtid_log_event gtid_event(thd, cache_data->is_trx_cache(), last_committed,
                            sequence_number, cache_data->may_have_sbr_stmts(),
                            original_commit_timestamp,
                            immediate_commit_timestamp);
    writer->write_full_event(buf, buf_len);
}


// 更新
MYSQL_BIN_LOG::process_commit_stage_quene(THD *thd, THD *first){
    for(){
        if( it->get_transaction()->sequence_number != SEQ_UNINIT /*0*/){
            m_depandency_tracker.update_max_commited(head)
        }
    }
}
```

*WriteSet写入 8.0特有*
```java
void handler_binlog_row(){ // handler_api.cc
    // ...
    switch(mode){
        case HDL_UPDATE: 
            log_func = Update_rows_log_event::binlog_row_logging_function;
            binlog_log_row(table, table->record[1], table->record[0], log_func);
            break;
        case HDL_INSERT:
            //..
            binlog_log_row(table, 0, table->record[0], log_func);
            break;
        case HDL_DELETE:
            //..
            binlog_log_row(table, table->record[0], 0, log_func);
            break;
    }
}

int binlog_log_row(table, before, after, log_function){ // handler.cc
    if(check_table_binlog_row_based(thd,table)){
        if(thd->variables.transaction_write_set_extraction != HASH_ALGORITHM_OFF){
            if (before_record && after_record) { 
                add_pke(table, thd);
                // swap table->record[0] <==> table->record[1] 
                add_pke(table, thd); 
            } else {
                add_pke(table, thd);
            }
        }
    }
}

void add_pke(table, thd){
    Rpl_transaction_write_set_ctx *ws_ctx = thd->get_transaction()->get_transaction_write_set_ctx();
    bool writeset_hashes_added = false;
    if( table-> key_info && table->s->primary_key < MAX_KEY /*max int*/){
        std::string pke_schema_table // pke_scheam_table = (db.name, len(db.name), table.name, len(table.name))
        for( key in table->s->keys ){
            if( key.flags & (HA_NOSAME) == HA_NOSAME) continue;
            std::string pke // pke = (key.name, pke_schema_table)
            for( key_part in key.user_defined_key_parts) {
                index = key_part.fiednr // column index in table
                pke.add( table->filed[index].make_sort_key() ) // append value of field to pke string
                pke.add( HASH_STRING_SEPARATOR )  // HASH_STRING_SEPARATOR=½
                pke.add( /*len of key value*/)
            }
            generate_hash_pke(pke, thd);
            writeset_hashes_added = true;
        }

        if( /* foreign key checks enable. && table has foreign key */ ){
            std::map // <column_name -> pke_perfix(unique_constraint, referenced_table_db, referenced_table_name) 
            check_foreign_key(table, thd , &map)
            for( key in table->s->fileds){
                it = map.find(key)
                pke = it->second()
                pke.append(/*value of column*/)
                pke.append(HASH_STRING_SEPARATOR)

                generate_hash_pke(pke, thd);
                writeset_hashes_added = true;
            }
        }
    }
}

static void generate_hash_pke(const std::string &pke, THD *thd) {
    uint64 hash = calc_hash(
      thd->variables.transaction_write_set_extraction, pke.c_str(), pke.size());
      // mysql variables transaction_write_set_extraction 
      thd->get_transaction()->get_transaction_write_set_ctx()->add_write_set(hash);
}
```