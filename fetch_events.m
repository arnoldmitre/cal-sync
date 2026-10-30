#import <Foundation/Foundation.h>
#import <EventKit/EventKit.h>

void requestAccess(EKEventStore *store) {
    dispatch_semaphore_t sema = dispatch_semaphore_create(0);
    if (@available(macOS 14.0, *)) {
        [store requestFullAccessToEventsWithCompletion:^(BOOL granted, NSError * _Nullable error) {
            dispatch_semaphore_signal(sema);
        }];
    } else {
        [store requestAccessToEntityType:EKEntityTypeEvent completion:^(BOOL granted, NSError * _Nullable error) {
            dispatch_semaphore_signal(sema);
        }];
    }
    dispatch_semaphore_wait(sema, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));
}

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        NSArray *args = [[NSProcessInfo processInfo] arguments];
        EKEventStore *store = [[EKEventStore alloc] init];
        requestAccess(store);

        if ([args containsObject:@"--list-calendars"]) {
            NSArray<EKCalendar *> *cals = [store calendarsForEntityType:EKEntityTypeEvent];
            NSMutableArray *items = [NSMutableArray array];
            for (EKCalendar *c in cals) {
                BOOL isEx = (c.type == EKCalendarTypeExchange) || [[c.source.title lowercaseString] containsString:@"exchange"];
                NSString *typeStr = @"Other";
                switch (c.type) {
                    case EKCalendarTypeLocal: typeStr = @"Local"; break;
                    case EKCalendarTypeCalDAV: typeStr = @"CalDAV"; break;
                    case EKCalendarTypeExchange: typeStr = @"Exchange"; break;
                    case EKCalendarTypeSubscription: typeStr = @"Subscription"; break;
                    case EKCalendarTypeBirthday: typeStr = @"Birthday"; break;
                    default: break;
                }
                [items addObject:@{
                    @"name": c.title ?: @"",
                    @"id": c.calendarIdentifier ?: @"",
                    @"source": c.source.title ?: @"",
                    @"type": typeStr,
                    @"isExchange": @(isEx)
                }];
            }
            NSData *data = [NSJSONSerialization dataWithJSONObject:items options:NSJSONWritingPrettyPrinted error:nil];
            NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
            printf("%s\n", [json UTF8String]);
            return 0;
        }

        if ([args containsObject:@"--get-events"]) {
            NSUInteger calIdx = [args indexOfObject:@"--calendar-id"];
            if (calIdx == NSNotFound || calIdx + 1 >= args.count) {
                printf("{\"error\": \"Missing --calendar-id\"}\n");
                return 1;
            }
            NSString *calId = args[calIdx + 1];

            NSInteger daysPast = 14;
            NSUInteger pastIdx = [args indexOfObject:@"--days-past"];
            if (pastIdx != NSNotFound && pastIdx + 1 < args.count) {
                daysPast = [args[pastIdx + 1] integerValue];
            }

            NSInteger daysFuture = 90;
            NSUInteger futIdx = [args indexOfObject:@"--days-future"];
            if (futIdx != NSNotFound && futIdx + 1 < args.count) {
                daysFuture = [args[futIdx + 1] integerValue];
            }

            EKCalendar *targetCal = [store calendarWithIdentifier:calId];
            if (!targetCal) {
                printf("{\"error\": \"Calendar not found with ID: %s\"}\n", [calId UTF8String]);
                return 1;
            }

            NSDate *now = [NSDate date];
            NSDate *start = [now dateByAddingTimeInterval:-daysPast * 86400];
            NSDate *end = [now dateByAddingTimeInterval:daysFuture * 86400];

            NSPredicate *pred = [store predicateForEventsWithStartDate:start endDate:end calendars:@[targetCal]];
            NSArray<EKEvent *> *events = [store eventsMatchingPredicate:pred];

            NSISO8601DateFormatter *iso = [[NSISO8601DateFormatter alloc] init];
            NSMutableArray *items = [NSMutableArray array];

            for (EKEvent *ev in events) {
                NSString *sStr = ev.startDate ? [iso stringFromDate:ev.startDate] : @"";
                NSString *eStr = ev.endDate ? [iso stringFromDate:ev.endDate] : sStr;
                NSString *status = @"none";
                switch (ev.status) {
                    case EKEventStatusCanceled: status = @"canceled"; break;
                    case EKEventStatusConfirmed: status = @"confirmed"; break;
                    case EKEventStatusTentative: status = @"tentative"; break;
                    default: break;
                }
                [items addObject:@{
                    @"uid": ev.eventIdentifier ?: [[NSUUID UUID] UUIDString],
                    @"summary": ev.title ?: @"(No Title)",
                    @"startDate": sStr,
                    @"endDate": eStr,
                    @"allDay": @(ev.isAllDay),
                    @"location": ev.location ?: @"",
                    @"description": ev.notes ?: @"",
                    @"status": status
                }];
            }

            NSData *data = [NSJSONSerialization dataWithJSONObject:items options:NSJSONWritingPrettyPrinted error:nil];
            NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
            printf("%s\n", [json UTF8String]);
            return 0;
        }

        printf("{\"error\": \"Unknown command\"}\n");
        return 1;
    }
}
